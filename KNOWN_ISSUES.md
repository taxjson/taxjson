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
- **When the lookup fails (2026-09 audit R1-105):** a Yahoo error, outage or rate limit, or an HTTP 200 with a null/empty close, leaves the row at price 0. That is never silent any more: fill-crypto warns (per lookup, plus an `UNPRICED` summary), and the crypto path runs `taxjson-validate --require-prices`, so each unpriced row is a validation ERROR on the console and fatal under `taxjson run --strict`. Re-run online (a failed price is never cached).

### IB ISIN→market map `IE → L` is wrong for non-LSE IE-domiciled ETFs
- **Where:** `src/taxjson/lib/brokerages/ib_extractor.py` — the module-level `_ISIN_EXT` map (`'IE': 'L'`), read through `_isin_ext()` by the Dividends and Withholding Tax branches (the Corporate Actions and Transfers branches derive suffixes via `_ib_currency_ext(currency)` instead).
- **Current behavior:** every Irish-domiciled (ISIN prefix `IE`) security is mapped to a `.L` (LSE) market suffix. Partially mitigated since the income-reattribution pass: DIVIDEND / DIVIDEND_IN_LIEU / TAX rows are re-bound to the suffix of the position actually held for that ticker in the statement (`_reattribute_income_to_holdings`; when the ticker is held under two listings during the statement, the one held on the payment date), so income no longer lands on a phantom `.L` symbol when the shares are held under another suffix. Since 2026-09 the holding may come from any of the account's IB statements (a statement with only a dividend row), and the rebind requires the held listing's ISIN (Financial Instrument Information) to match the income row's — a different issuer sharing the ticker keeps its own listing.
- **Why deferred:** the user holds no IE-domiciled ETFs, so the bug doesn't fire on their data. Most IE-domiciled ETFs trade in EUR / multiple currencies, not all on LSE; a real fix needs an ISIN → exchange lookup or a per-ticker override.
- **Workaround:** users who hold IE-domiciled ETFs should add a `ticker.map` GLOBAL rule rewriting the parsed `.L` symbol to the correct market suffix.

### IB cash-in-lieu row wording is unverified
- **Where:** `src/taxjson/lib/brokerages/ib_extractor.py` — `_IB_CIL_RE` in the Corporate Actions branch.
- **Current behavior:** a Corporate Actions row whose description contains the phrase `cash in lieu` (any case, anywhere after the leading `TICKER(ISIN)` token) with a negative Quantity is booked as a sale of that fractional quantity for the row's Proceeds (a blank Proceeds is refused; a 0 is booked as 0 with a warning — IB's Value is a market value, never cash), and the fraction is folded into the leg-derived ratio of the same symbol's split nearest its date, within a week (in either row order), so the pool ends on whole shares; a fraction no split claims is said in a note. The regex was written against a synthesized row (`TINY(US…) Cash in Lieu of Fractional Shares (TINY, TINY CORP, US…)`, quantity `-0.3333`, proceeds `3.10`) — no real IB statement with such a row was available.
- **Risk:** if IB words the row differently (no `cash in lieu` phrase, or the fraction/cash in other columns), the row falls into the unhandled corporate-actions tally (warned at end of parse) and the fractional dust stays in the pool — the pre-fix behavior, not a silent mis-booking.
- **Evidence needed:** a real IB Activity Statement CSV containing a reverse split (or merger) with its cash-in-lieu row; attach it to graduate this item.

### IB `Trades / Forex` conversions are not modeled
- **Where:** `src/taxjson/lib/brokerages/ib_extractor.py` — the Trades branch, asset category `Forex` (rows like `Trades,Data,Order,Forex,CAD,U1,USD.CAD,"…",<qty>,<T. Price>,…`).
- **Current behavior:** an explicit currency conversion is COUNTED as a recognized non-event (the calmer `taxjson-brokerage` note: `Trades/Forex (currency conversion, not modeled — KNOWN_ISSUES)`) and not translated. No phantom `USD` / `CASH.USD` asset is emitted — doing so would put a fake position in the book.
- **Why deferred:** `taxjson fx-cash` (`src/taxjson/bin/taxjson_fx_cash.py`) reconstructs foreign-cash ACB from the security cash flows in the taxable books, and its docstring assumes broker CSVs carry no explicit conversions — IB's do (this section), so on an IB account the ledger is asked to spend currency it saw acquired only through trades and overdrafts on the conversion side. Consuming Forex rows properly means booking each as a disposition of the sold currency at the conversion rate AND an acquisition of the bought one, together with the cash deposits/withdrawals the same statement lists — half of that (conversions only) would still overdraft.
- **Evidence / work needed:** extend the fx-cash ledger to read Forex rows plus the `Deposits & Withdrawals` section as currency acquisitions/dispositions; until then the IB Forex count in the parse note is the size of the gap.

### fx-cash: cash folded into a corporate-action sale leg is not ledgered
- **Where:** `src/taxjson/bin/taxjson_fx_cash.py` — `_non_cash`.
- **Current behavior:** rows emitted by the corp-actions stage (`corp_event_id` set: share-for-share mergers, taxable exchanges at FMV, spin-off ACB allocations) move no foreign cash and are left out of the s.39(1.1) ledger; so are crypto-for-crypto legs (Kraken swaps, Coinbase Convert) and staking rewards paid in a coin. A standalone cash-in-lieu leg is ledgered. Cash-in-lieu or §356 boot FOLDED into a taxable exchange's sale leg (`_emit_taxable_exchange`, `_emit_boot_exchange`) is not — the row does not say how much of its proceeds was cash. A cash takeover is a sale the broker parser books and is ledgered normally.
- **Evidence / work needed:** emit the cash part of a taxable exchange as its own leg (or a `cash_amount` field) so the ledger can count it; the amounts are fractional-share dust in practice.

### Kraken fiat conversions are not modeled
- **Where:** `src/taxjson/lib/brokerages/kraken.py` — `_parse_trades` (a fill whose BASE is fiat after stablecoin folding: `USD/CAD`, `USDC/USD`, `USDT/CAD`) and `_build_instant_trade` (a `spend`/`receive` pair whose both legs are fiat: USDC dust swept to USD, USD → CAD).
- **Current behavior:** counted as recognized non-events (`forex conversion … not modeled — KNOWN_ISSUES`). Previously each emitted a BUYSELL of a phantom `USD` / `CAD` asset (the fiat base treated as the traded security), which put a fake position in the crypto book and a nonsense trade in the gains report.
- **Why deferred:** same reason as the IB item above — foreign-cash gains live in `taxjson fx-cash`, which does not read conversion rows yet. Stablecoin↔USD swaps are additionally a wash by construction (folded 1:1 for pricing) — in a Canada project; a fill more than 2% off 1.00 USD prints a de-peg warning.
- **US projects differ:** stablecoins are property there (tax-logic US-CRYPTO-02): `taxjson run` parses with `--country usa`, so a `USDC/USD` fill, a `Buy`/`Sell USDC` row, a swap against a stablecoin, a stablecoin reward or fee are booked as purchases and sales of the coin (a swap, reward or fee at the 1.00 USD par; a sale for dollars at its price). Kraken ledger-only instant trades between a stablecoin and dollars follow the same rule.
- **Coinbase follows the same model:** `Buy USDC` / `Sell USDC` rows are counted as stablecoin conversions (non-events) and the USDC leg of an Advanced Trade on a `*-USDC` pair is cash, not a position. An Advanced Trade on a crypto-quoted pair (`ETH-BTC`) is a swap: the quote coin's leg is booked too, at the fill's stated value (2026-09 audit R1-102). Strictly (CRA) a stablecoin is a crypto-asset, so the USD/CAD movement while USDC is held is an unbooked gain/loss — a few dollars a year on real data.

### Canadian listings carry no venue (`ROOT.TO` for TSX, TSXV, CSE and NEO)
- **Where:** `src/taxjson/lib/brokerages/base.py` — `canonical_ca_listing`, used by `apply_currency_suffix` (Questrade, RBC, Webull, generic) and `taxjson_fetch.qt_position_symbol`; IB stamps every CAD listing `.TO`.
- **Current behavior:** one Canadian security has one symbol whichever broker reports it: `ROOT.TO`, with a TSX preferred series dotted (`FTN.PR.A.TO`). RBC and Webull exports do not name the venue, and real books (ticker.map `TOBASE` rules) are keyed on `.TO`, so the venue suffixes `.V` / `.CN` / `.NE` are not used as identities. A `.tt` line is read the same way (`ABC.V` on a CAD line, `ABC.VN`, `FTN.PRA.TO` become `ABC.TO` / `FTN.PR.A.TO`). A ticker.map rule that writes `ROOT.V` still splits the pool from `ROOT.TO`; `taxjson-lint-crosslistings` flags it (CANADIAN VENUE SPLIT), `.VN` and undotted preferred series included.
- **Why this is the choice:** the alternative (venue suffixes everywhere) needs every parser to know the venue; RBC and Webull cannot, and IB would rename the owner's Venture/CSE holdings. TSX and TSX Venture share one symbol namespace, so `.TO` is unambiguous for Venture names; a CSE/NEO ticker that duplicates a different TSX ticker would share a pool (as it already did in IB statements).
- **Workaround:** price lookups that need the venue use `yf_ticker.map` (`PNG.TO PNG.V`).

### Trade reversals across export files
- **Where:** `src/taxjson/lib/trade_cancel.py` (IB `Ca`), `src/taxjson/lib/brokerages/questrade.py:_pair_reversals` (CIL / REI / stock dividend).
- **Current behavior:** an IB cancellation pairs with its original in the same statement or, through `taxjson-merge2`, in another statement of the same account; with no original anywhere it stays booked as a reversing trade and merge2 warns. An OVERLAPPING statement of the same IB account (a download taken before IB posted the cancellation) that still holds the original drops it too, for Trades, Transfers and Corporate Actions rows, so dedup keeps one book (`IbBrokerage.reconcile_files`); statements of different IB accounts (Account Information) never touch each other. A Questrade CIL/REI/stock-dividend reversal must find its original in the SAME export file, else the parse is refused.
- **Why deferred:** no real Questrade reversal row has been seen, so its cross-file shape (same code, negated signs, later date) is inferred from how Questrade reverses dividends.
- **Workaround:** delete both rows of a reversal pair that straddles two exports, or book the correction in a `.tt` file.

### A negative futures price in a generic or `.tt` file
- **Where:** `src/taxjson/lib/brokerages/generic.py` (`_trade_net` takes the magnitude), `src/taxjson/bin/taxjson_convert_tt.py` (a `.tt` total is a magnitude).
- **Current behavior:** IB futures rows keep the sign of a negative price (audit A2-0092); a generic-import or `.tt` futures row at a negative price is still read as its magnitude, so its P/L sign is wrong.
- **Workaround:** book such a fill from the IB statement, or enter the realized P/L of the close by hand.

### A warrant exercise is booked as a disposal at 0
- **Where:** `src/taxjson/lib/brokerages/ib_extractor.py` (a `Warrants` leg coded `Ex`/`A` at price 0), `src/taxjson/lib/brokerages/rbc_direct.py` (an `Exercise` of a non-option symbol); the premium roll in `lib/core.py` handles OPTION symbols only.
- **Current behavior:** the warrant leg is a disposal at 0, so the warrant's cost is a capital loss on the exercise date and the shares carry only the cash paid; the correct treatment is no disposition and the warrant's cost added to the shares (ITA s.49(3); US basis carryover with a holding period from the exercise). IB prints an `ATTENTION` line for the leg (audit A2-0090 / A2-0274).
- **Why deferred:** the engines' premium roll keys on OCC option symbols; a warrant needs its own pairing with the exercised shares (parser marks the pair, both engines roll the cost, dual-country tests). No warrant exercise is in the owner's books.
- **Workaround:** book the exercise by hand: drop the warrant leg and add the warrant's cost to the shares' purchase in a `.tt` file.

### Identical rows in two exports with little overlap are booked once
- **Where:** `src/taxjson/bin/taxjson_sort.py` — `plan_dedup`, used by `taxjson-merge2 --dedup`, `taxjson-sort --dedup` and `fees-sum`.
- **Current behavior:** the same row in two exports of one account is booked once. Sometimes the files' overlap cannot show that they are copies of one export: they share only that row, or each holds rows the other lacks on the dates both cover, or a `.tt` line equals an exported row. The row is still booked once, as a re-export, and `taxjson run` prints `warning: ATTENTION: dedup: ...` naming both files and the row. Exports are cut by date, so two exports of ONE broker account can only both hold one fill when they both cover its whole day, and then both files hold all of that day's fills; two separate identical trades of one account can only end up split across files when an export is cut up by hand. Rows of two different broker accounts are never collapsed: every parser that reads the account (IB, Questrade, RBC, Webull, a generic mapping that names it) stamps it on each row. Identical lines in two `.tt` files are both booked. Kraken and Coinbase exports name no account; their rows carry the exchange's own transaction id, so identical rows do not arise.
- **Why this is the choice:** an export carries no row id. Booking such a row twice would double-count the common case, a boundary day that both exports include.
- **Workaround:** if the ATTENTION line names two separate trades, enter the second one as a `.tt` line.

### Kraken fees taken in the traded coin are not in the fee reports
- **Where:** `src/taxjson/lib/brokerages/kraken.py` — `_parse_trades` (a fill whose ledger shows the fee taken in the base coin) and the ledger instant-trade path (a crypto leg's fee).
- **Current behavior:** the fee coins are folded into the quantity (fewer coins received on a buy, more given on a sale) and the fill's `fee` field is 0, so cost basis and proceeds are right, but `fees.rpt`, `taxjson fees-sum` and the `.sum` FEES line leave these fees out (on real 2025 data more than half of the Kraken trading fees). The parse note says so.
- **Why deferred:** the `fee` field feeds the engine's per-row fee figures; recording a fee already inside the quantity there needs an informational-only fee field first.

### Questrade `commission` vs everyone else `fee`
- **Where:** `src/taxjson/lib/brokerages/questrade.py`.
- **Current behavior:** Questrade transactions emit a `commission` key; IB / RBC / Webull / Kraken / Coinbase all emit `fee`.
- **Why this isn't a bug:** cost-basis math is correct because downstream sums both fields (`core.py`'s `_effective_fee_for_trace` uses `tx.commission + tx.fee`). The inconsistency is cosmetic — per-row reports that itemize one column show the values under different headers across brokerages.
- **Why deferred:** pure refactor with no behavioral change. Touching every test and downstream consumer for a cosmetic split isn't worth the churn.

---

## Cross-parser asymmetries (from the 2026-06 flow audit)

Capabilities one broker parser has that a comparable one lacks. The ones below are deferred because they need a real broker sample to implement safely, or are a design decision.

### Webull exercise/assignment inference
- **Where:** `src/taxjson/lib/brokerages/webull.py` — `_mark_assignments`.
- **Current behavior:** Webull's Trading Summary shows an exercise or assignment only as a $0 option close plus an ordinary stock trade at the strike. The parser pairs them (both legs ASSIGN, premium folded into the shares under s.49(3)) when the stock trade is on the same underlying (the row's own `@Symbol`), for 100 x contracts shares in the matching direction, at the strike, settling -1..+7 days from the close, AND carries Webull's $1.00 exercise/assignment charge; the smallest settle gap wins across every option, and exports beside the file are searched too (a Dec-31 assignment whose shares settle in January). Every inferred pair is named on stderr. A trade at the strike with an ordinary commission is NOT paired (a limit order at a round strike after a worthless expiry) and is named as a warning instead; so is a $1.00-charge trade whose quantity does not match the close one-to-one (2 contracts closed against two 100-share rows, or two 1-contract closes against one 200-share row) — book that one by hand. A paired option leg settles with its stock leg (CA-DATE-04 / US-DATE-04). A $0 option row is accepted only as a close: at the expiry, or earlier as a paired exercise/assignment (an unpaired early $0 close is named); a $0 row that opens a position, and any share row with no Price and no Proceeds, is refused. Exports of a different Webull broker account (the preamble's Account Number) are never paired with each other.
- **Why this is the choice:** the export has no action code for exercise/assignment; the $1.00 charge is the only evidence that separates a real one from a coincidental trade. If Webull changes that charge, a real assignment is booked as an expiry plus a trade, with the warning naming it.

### Webull Trading Summary carries no income
- **Where:** `src/taxjson/lib/brokerages/webull.py` — the Trading Summary holds BUY/SELL rows only.
- **Current behavior:** Webull interest and dividends (T5 slips) are not in any Webull input, so the account's income summary leaves them out. A row with another action code (DIV, a transfer) that does appear in a Trading Summary is reported as `warning: UNBOOKED:` (echoed by `taxjson run`, refused by `--strict`). Enter them by hand in a `.tt` file in the account's folder: `INTEREST 2025-12-31 16:00:00 USD 1149.27` (T5 box 13; a slip with a blank box 27 is CAD), `DIVIDEND ...` for dividends.
- **Why:** Webull exports no income file the parser could read.

### Questrade emits no standalone INTEREST or withholding-TAX rows
- **Where:** `src/taxjson/lib/brokerages/questrade.py` — strips `TAX WITHHELD`/`NON-RES` only as description-key noise; no TAX/INTEREST emission.
- **Current behavior:** IB and RBC emit dedicated TAX (foreign withholding) and INTEREST records; Questrade does not, so a Questrade account's non-resident-tax-withheld dividend or interest credit is not recorded as such (foreign-tax-credit / interest income under-reported).
- **Why deferred:** needs a Questrade CSV showing the interest and withholding row formats to parse them correctly.

### RBC identity across projects (one year's export per project)
- **Where:** `src/taxjson/lib/brokerages/rbc_direct.py` (`build_rbc_account_context`).
- **Current behavior:** the RBC parser learns identities from ALL of an account's RBC exports in the project: a symbol's listing, an option code's contract, a temporary reorganization code's company, and overlapping re-downloads of the same RBC account. When a project holds only the current year's export and the earlier years come in through a hand-written `.tt` (`margin_start.tt`), the earlier rows are not there to learn from. What the parser does then: income on a symbol that no file trades keeps the payment currency's listing (a USD return of capital there is an ATTENTION line on the run console with the `TOBASE` line that fixes it); a temporary removal code it cannot name is assumed to be the receipt's ticker, with an ATTENTION line and the `ticker.map` line to fix it; an option that RBC re-describes between years (RCI vs RCI.B, an XCH-adjusted TRP1) keeps the description of this year's rows, so the `.tt` must use the same symbol — a closing row (RBC's `CLOSE CONTRACT`, an expiry, an assignment) the books cannot back is an ATTENTION line on the run console naming the contract held under the other root and the `GLOBAL` line that joins them, and `taxjson handoff` accepts the re-described root (and fails the closed year's root when this year's export closes the other one). A ticker change RBC applied without a reorganization row (ORCC to OBDC) is only an ATTENTION line with a ready `GLOBAL` line, because the export carries no CUSIP to prove the two symbols are one security.
- **Workaround:** keep the earlier years' RBC exports in the project's `inputs/<account>/`, or add the suggested `ticker.map` line.

### Parser identity hints still print after the ticker.map line is added (re-audit A2-1056)
- **Where:** `rbc_direct._detect_ticker_changes`, `_report_untraded_income`; `questrade._detect_qt_ticker_changes`; `webull._warn_ticker_changes`.
- **Current behavior:** the parsers run before `ticker.map` is applied and never see it, so a ticker-change or listing hint stays in the run console and the `.sum` after you add the suggested line (the positions are right).
- **Planned fix:** pass the map to `taxjson-brokerage` (`--ticker-map`, as `taxjson-corp-actions` already takes it) and drop a hint whose pair the map already joins.

### Blank settlement cells fall back to the row currency's calendar (re-audit A2-1052 / A2-1054)
- **Where:** `questrade.py` / `rbc_direct.py` blank-settle fallbacks and IB's `get_ib_settlement` (no settle column at all); the generic importer already uses the listing.
- **Current behavior:** a USD trade of a TSX listing (DLR.U.TO) with a blank Settlement Date settles on the US calendar (Canada Day is skipped only by the CDS calendar). Only blank cells are affected for Questrade and RBC.
- **Planned fix:** one helper (listing suffix -> market, else the row currency) for every parser, IB included, with CA-DATE-04/05 and US-DATE-04/05 saying so.

### RBC in-kind transfers are emitted but excluded from taxable accounts (by design)
- **Where:** `src/taxjson/lib/brokerages/rbc_direct.py:_build_transfer` (added 2026-06); `taxjson-brokerage` drops TRANSFER rows unless `--transfers`, driven by `transfers` in `[accounts.<name>]`.
- **Current behavior:** RBC now emits a TRANSFER for an in-kind security move (Activity `Transfers`, e.g. a DTC transfer-in). For sheltered accounts (`transfers = true`) it's kept; for **taxable** accounts `transfers` stays **off**, so the row is dropped.
- **Why this is intentional (decided 2026-06):** taxable cost basis must be computed from *actual* buys and sells — a transfer-in carries no reliable ACB (RBC's Value column is 0; the description's "BOOK VALUE nnn" is the sending side's book cost, which may not be the ACB — it is kept as `book_value` evidence, shown by `taxjson transfers`, and never booked), so accepting it would fabricate basis. Dropping it instead leaves the position looking short until the user supplies the real acquisition history; that phantom short is the **correct signal** (surfaced by `taxjson-missing-history`) that actual buys are missing, not something to paper over with a transfer. Do not flip the taxable default.

### IB income rows carry no record date
- **Where:** `lib/brokerages/ib_extractor.py` (the Dividends section has only the pay date); `lib/income_dating.py`.
- **Current behavior:** in a Canada project a Canadian trust's distribution or return of capital is dated by the record date Questrade and RBC print (s.104(13), s.53(2)(h); tax-logic CA-INC-DATE-TRUST / CA-INC-DATE-ROC-TRUST). An IB row has no record date, so a December-record trust distribution IB pays in January stays in the pay year of `divs-sum` and the estimate, and a January-paid IB ROC on a Canadian trust stays on its pay date — the run warns about the ROC with the two `.tt` ADJUST lines that move it to Dec 31.
- **Also:** a Canadian issuer is recognised by its listing (or an IB CA ISIN); the split-share corporations that also say "Distribution" are a short built-in list (`SPLIT_SHARE_ROOTS`) — add any other corporation to `[settings] corporate_distributions`.
- **Why the pay date is kept (owner decision 2026-10-01, audit S057-23):** IB's Dividends section prints only the pay date and labels a trust's distribution a cash dividend, so nothing on the row says the payer is a trust rather than a corporation or split-share issuer (which s.82 dates when paid). The ex date IB's "Change in Dividend Accruals" section gives is kept on the row (`ex_date`, a US project reads it for §852(b)(7)) but is not a record date and does not identify a trust, so a Canada project does not date income by it. Every January CAD payer in the owner's IB books is a corporation, so no current return moves.
- **Workaround:** compare `divs-sum` with the T3; the slip is authoritative.

### RBC exports by Date miss back-dated year-end book-cost rows
- **Where:** the RBC export window (not the parser: `rbc_direct.py:_build_book_adjust` books the rows correctly when present).
- **Current behavior:** RBC posts year-end book-cost adjustments ("2022 NOTIONAL DISTRIBUTION ADJUSTMENT TO BOOK COST", a year-end ROC) dated Dec 31 but only in the following spring. An export for the calendar year taken before then, and next year's export (which starts Jan 1), both lack them (2026-09 audit R1-85: a 2023 VDY loss understated by 5,291.90). Since 2026-10 the parse reads each export's "Activity Export as of" stamp: when every export holding the tax year's rows was taken before June 30 of the next year and the account held a position at the year end, the account's `.sum` carries a note saying the adjustments may be missing (audit S063-22). The rows themselves cannot be seen until RBC posts them; an export taken before Dec 31 of the year is an ATTENTION on the console, and `checklist` inputs-frozen checks each account's latest RBC export against Jan 31.
- **Workaround:** export each RBC year with an end date after the following June (or re-export the prior year once the T3s are out) and keep the overlapping files: overlapping downloads of one account are de-duplicated row by row.

### RBC notional distributions raise ACB only
- **Where:** `src/taxjson/lib/brokerages/rbc_direct.py:_build_book_adjust`.
- **Current behavior:** a "NOTIONAL DISTRIBUTION ADJUSTMENT TO BOOK COST $x" row becomes an ACB increase (ADJUST `dist`), and the parse warns that the distribution itself is income on the fund's T3 (usually box 21) and is NOT in taxjson's income totals (2026-09 audit S063-17). Stated in tax-logic (CA-DIST-02 / US-DIST-02).
- **Why (owner decision 2026-09-30, D6: stays a warning):** booking it as income needs its character (capital-gain distribution vs other income), which only the T3 gives; booking it as a dividend would gross it up as eligible. Take the amount from the slip.

### Stock dividends: $0 in Canada until the declared amount is added
- **Where:** the parsers emit a neutral stock-dividend event — a $0 BUYSELL of the new shares typed `stock_dividend` — from `questrade.py` (the `DIS` + stock-dividend branch), `ib_extractor.py` (a Corporate Actions `Stock Dividend` row; IB's exact wording is modelled, not seen in a real statement; its `ATTENTION` line shows the row's Value) and `rbc_direct.py:_build_stock_dividend`. Each gains engine applies its country's rule (`lib/core.STOCK_DIVIDEND`).
- **Current behavior:** Canada: the shares enter the pool at $0 cost and count as an acquisition for the superficial-loss rule (tax-logic CA-STKDIV-01); the taxable amount is the fund's *declared* amount, which the CSV does not carry, and the gains run prints a `NOTE:` naming the symbol, date and share count. US: a pro-rata stock dividend is not income (§305(a)) — the new shares join the lots held, the basis is spread over old and new (§307), the purchase dates carry over (§1223(5)), and they are not a §1091 purchase (US-STKDIV-01). A taxable US stock dividend (§305(b)) is not detected. An IB stock dividend whose trailing `(TICKER, NAME, ISIN)` names ANOTHER security (another class) is not booked: an `UNBOOKED` line asks for a hand entry (a split whose new leg names another ticker renames the pool; a cash in lieu naming another security sells that security's fraction).
- **Impact:** registered accounts — none. Canadian taxable accounts — ACB is understated (gain overstated at sale) until the declared amount is supplied; the zero-basis walk also surfaces the position via `taxjson find-missing-history`.
- **Workaround (the intended flow, Canada):** add the fund's declared per-share amount for the record date to `distributions.map`; `taxjson run` converts it into the ACB-raising ADJUST. That books the cost side only: the declared amount is also a dividend of the year, reported from the T5/T3 slip — taxjson's income totals, `divs-sum` and `estimate` do not include it (the run's NOTE says so).

### Settlement calendars outside North America skip weekends only; the generic importer keys the market on currency
- **Where:** `src/taxjson/lib/dates.py` (`_T1_CUTOVER`), `src/taxjson/lib/market_calendar.py`, `src/taxjson/lib/brokerages/generic.py`.
- **Current behavior:** the settlement lag follows the market: USD/CAD/MXN T+1 since May 2024; GBP/EUR/CHF T+2 until the 2027-10-11 move to T+1; every other market (the ASX, HKEX, Tokyo, ...) T+2. Outside the US and Canada only weekends are skipped — a local bank holiday inside the lag (Jan 1, Boxing Day) is not, so such a settle date can be a day early. The IB parser takes the market from the listing (a USD unit on the TSX settles on the Canadian calendar, a USD line listed on the LSE is `.L` on the UK cycle) and dates ASX, HKEX, Tokyo, Singapore and NZX fills in the exchange's local time (tax-logic CA-DATE-SESSION). The generic importer still keys the market on the row currency for a suffix other than the Canadian ones and `.US`, so a USD-quoted `.L` row settles on the US T+1 cycle; other non-North-American venues (Eurex, the LSE in the evening) keep IB's Eastern clock date.
- **Why deferred:** per-market holiday calendars and venue time zones for markets the books rarely touch.

---
---

## Engine scope (documented out-of-scope rules)

### US: §1091(e)(1) — a long SALE within the window of a short-cover loss is not a wash trigger
- **Where:** `src/taxjson/lib/core.py` US short-side replacement matching (`short_replacements`): only the short-OPEN portion of a SELL (§1091(e)(2), "another short sale") registers as a replacement for a loss on closing a short.
- **Current behavior:** short 100 @100; cover 200 @110 (loss −1,000, opens 100 long); sell 100 @105 two weeks later → the −1,000 cover loss is allowed. §1091(e)(1) ("substantially identical stock ... were **sold**" within the window) would wash it. Same-year totals coincide; cross-year attribution and 8949 code-W reporting can differ.
- **Why deferred:** rare shape (a cover that flips long, then a sale inside the window); documenting the gap is the honest state until a fixture demands it (2026-09 US-engine audit). `taxjson tax-logic` states it (US-WASH-19, audit A2-0062).

### US: sheltered (IRA) replacements already sold before the loss still deny it; taxable ones don't
- **Where:** `core.py` US pass — sheltered BUYs register their full quantity with no lot reference and are never decremented by later sheltered SELLs; taxable replacement lots are zeroed on consumption.
- **Current behavior:** IRA buys 100 on 05-20 and sells 100 on 05-25; taxable loss 06-15 → `permanently_disallowed`. The identical pattern in a second taxable account (`per_account_basis`) → loss allowed. §1091(a) keys on ACQUISITION within the window (no still-held test), so the IRA reading is the literal statute and the taxable reading follows Reg. 1.1091-1's lot consumption — the two books apply different theories.
- **Why deferred:** which reading is right for shares acquired AND disposed inside the window before the loss is not settled authority; flagged so the asymmetry is known (2026-09 audit). `taxjson tax-logic` states the IRA reading (US-WASH-11, audit A2-0962).

### US: options as replacement property are advisory-only
- **Where:** `core.py` `detect_option_replacement_matches` (warn-only in the US engine; the Canada engine enforces the call rule).
- **Current behavior:** §1091(a) covers "a contract or option so to acquire"; a deep-ITM call bought inside the window leaves the stock loss allowed, with a warning. A user policy choice, not a bug — the statute itself is mandatory, so treat the warning as an instruction (2026-09 audit).

### US: §355 spin-off basis is spread by quantity, with no per-block tacking (A2-0065)
- **Where:** `lib/corp_actions.py` `_us_spinoff_tax_free_355` (one BUYSELL of the spin-off on the spin date plus one parent ADJUST) and the US engine's ADJUST branch in `core.py` (spread per share across the open lots).
- **Current behavior:** the allocated basis is taken from each parent lot in proportion to its SHARES, not its basis (Reg. §1.358-2: each share gives up the same fraction of its own basis), so a low-basis lot can go below zero and book a §301(c)(3) "deemed gain" on a tax-free spin-off; and the spun-off shares are one new lot dated on the spin date instead of one block per parent lot with the parent's holding period (§1223(1)).
- **Why deferred:** needs the engine to apply a basis-allocation event per parent lot (a fraction of each lot's basis, and a spin-off lot per parent lot carrying its acquisition date); the corp-actions stage does not see lots. Workaround: book the spin-off in a `.tt` file as one BUYSELL per parent block with the block's date, and a per-lot ADJUST.

### US: `reorg_368_boot` is computed on the whole pool, not per block (A2-0066)
- **Where:** `lib/corp_actions.py` `_emit_boot_exchange` (one engineered SELL at proceeds = total basis + recognized gain, split by the engine across lots by quantity).
- **Current behavior:** with lots of different basis, one lot books a gain and another a LOSS, though §356(c) recognizes no loss; Reg. §1.356-1(b) / Rev. Rul. 68-23 compute the recognized gain block by block (each block: min(its realized gain, its share of the boot), never below zero). Totals are right only when every lot is in a gain.
- **Why deferred:** needs per-lot data the corp-actions stage does not have; the fix is an engine-applied boot exchange (per lot: realized = its share of new-share FMV + boot − basis, recognized = max(0, min(realized, boot share)), new basis = basis − boot share + recognized, holding period tacked). Workaround: book it by hand in a `.tt` file, one SELL/BUY pair per block.

### US: specific-lot identification is not supported (FIFO only)
- **Where:** the US engine consumes lots FIFO (Reg. 1.1012-1(c)(1) default). Reg. 1.1012-1(c)(2)–(3) specific identification, and a broker's non-FIFO default (e.g. highest-cost), are not modeled.
- **Consequence:** a broker 1099-B computed under specific ID will not reconcile per-lot; year totals agree only when every lot is eventually sold. Set the broker's lot method to FIFO or reconcile by hand.


### §1256 (60/40 mark-to-market) is not implemented
- **Where:** `src/taxjson/lib/core.py` — documented out-of-scope in the US engine's docstring, alongside the §1233(b)(1)/(2) (long held ≤1 year) and §1233(d) (long held >1 year) short-sale rules and §1259 constructive sales.
- **Current behavior:** futures and broad-based index options (SPX, NDX, futures) are run through the ordinary FIFO ST/LT engine — no year-end mark-to-market, no 60/40 split. The US filing outputs (`form-export --form 8949` / `txf`, `sum` FOR THE RETURN, the close-year lock) recognise them (`lib/futures.section_1256_kind`: plain futures, options on futures, a short list of broad-based index option roots) and keep them OFF Form 8949, listing each with its P/L for Form 6781.
- **Why deferred:** needs a mark-to-market pass for contracts open at year end; no user data currently exercises it.
- **Workaround:** report §1256 contracts on Form 6781 from your broker's 1099-B (they're reported mark-to-market there); the export lists them but does not split or mark them. An index option whose root is not in the list is filed as an ordinary option — check it.

### US: January-paid Q4 fund dividends are dated in the pay year
- **Where:** `src/taxjson/lib/pipeline.py` (income belongs to the year it was received — the pay date), shared by `divs-sum`, `sum-income` and the US estimate.
- **Current behavior:** a RIC/REIT dividend declared in October–December with a December record date and paid in January (IRC s.852(b)(7); REITs s.857(b)(9)) counts in the PAY year. The 1099-DIV puts it in the prior year, so the dividend totals and the US estimate shift about one quarter's ETF distribution between years (audit S076-23). Dividends are not form-exported, so no form number is affected.
- **Why deferred (owner decision):** broker exports carry the pay date and rarely the record/declaration date, and nothing in the books says whether a security is a RIC/REIT. Options: (a) a per-project override list of January payments to move to Dec 31; (b) a heuristic for US-listed ETFs/funds with a January pay date and a December ex-date when the export has one; (c) keep pay-date dating and reconcile against the 1099-DIV by hand.

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

### Purchases by an affiliated person (spouse, controlled corporation) are not an input of `taxjson run`
- **Where:** `taxjson.toml` account types are `taxable | sheltered`; `taxjson run` never passes affiliated trades to the engine (`taxjson-gains` / `-explain` / `-audit --affiliated` take them, and the engine applies them).
- **Current behavior:** a loss whose identical property your spouse (or a corporation you control) buys within 30 days is ALLOWED unless their trades are in the project — s.54 "superficial loss" covers an acquisition by an affiliated person (US: §1091 reaches a spouse's purchase too). Nothing warns.
- **Why deferred (owner decision):** a first-class affiliated account type would need its own book that is never reported as yours; until then the tool cannot see trades it is not given.
- **Workaround:** add the affiliated person's account as `type = "sheltered"`: the loss is then denied (permanently for you, as s.53(1)(f) puts the ACB bump on the affiliated holder). The account then also shows in the SHELTERED tables and the radar as if it were your registered plan — read it as theirs.

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

### Estimate has no input for a minimum tax carryover
- **Where:** `taxjson estimate` / `taxjson instalments` (`lib/tax_estimate.py`).
- **Current behavior:** minimum tax (AMT) paid in the 7 preceding years is creditable against regular tax above the minimum (ITA s.120.2; T691 Part 8, T1 line 40427, and the provincial piggyback such as ON428 line 59). The estimate cannot take that carryover, so in a year where regular tax exceeds the minimum it overstates tax — and the current-year instalment basis, which uses total tax, overstates by the full credit. When AMT does not bind, the estimate prints a NOTE with the headroom a carryover could use.
- **Workaround:** subtract the carryover you can apply (from your T691 / notice of assessment) by hand.

### Estimate: credits, OAS recovery tax and AMT adjustments outside the books
- **Where:** `src/taxjson/lib/tax_estimate.py` `estimate_canada`, `_amt_canada`.
- **Current behavior:** the only non-refundable credit modelled is the basic personal amount (with its phase-down) — CPP/EI, the Canada employment amount, the age and pension amounts and donations cannot be entered, so an employee's instalment basis is too high and a senior's age-amount clawback caused by investment income is missing. There is no Old Age Security input, so the s.180.2 recovery tax that investment income can trigger above the threshold is left out (the estimate and the current-year instalment basis are too LOW for an OAS recipient). The AMT check sees only the books and `other_income`: the s.110(1)(d) stock-option deduction that s.127.52(1)(h) adds back, donated securities' 30% inclusion and credits other than the BPA are not modelled, so it can say "not binding" when AMT binds (audit S077-15, S077-17, S077-20). The printed assumptions line says so.
- **Workaround:** fold the effect into `other_income` / `--deductions` by hand, or treat the estimate as an investment-income-only figure.

### Estimate and instalments: FX on cash, slip gains, self-employment CPP/EI
- **Where:** `taxjson estimate` / `sum --estimate` / `taxjson instalments`.
- **Current behavior:** the estimate counts the books' dispositions and income only: the FX result on foreign cash (s.39(1.1), line 15300 — `taxjson fx-cash`; US §988) and capital gains reported on T3/T5 slips are not in it, and the instalment schedule has no input for CPP/EI payable on self-employment earnings (lines 42100/42120), which CRA adds to the instalment amount (not to net tax owing or the $3,000 test). The assumptions line and an instalments `NOT MODELLED` note say so (audit S048-12, S043-15).
- **Workaround:** fold a material FX or slip gain into `other_income`, and add self-employment CPP/EI to the instalments you pay.

### Interest expense and carrying charges are not surfaced
- **Where:** IB `INTEREST` rows keep their sign; `sum-income` nets debit against credit interest.
- **Current behavior:** margin interest paid (deductible under s.20(1)(c), line 22100; only 50% for the 2024+ AMT) disappears into the income total instead of being reported as a deduction. The estimate does not read interest from the books; enter the year's carrying charges yourself (`taxjson estimate --carrying-charges`, or `[estimate] carrying_charges`), which it deducts in full from regular income and at 50% in the AMT base.

### Spin-off default wording
- **Where:** `lib/corp_actions.py` spin-off default.
- **Current behavior:** the default books a spin-off as a dividend at FMV; the estimate then classifies it by the target's suffix (a Canadian parent's in-kind distribution is an eligible dividend). A Canadian parent's tax-deferred spin-off (a butterfly / s.86 reorganisation) has no election of its own: book it with `rollover_s_86_1` and the allocated ACB, which moves cost the same way.

### IB stock-plus-cash mergers are not booked
- **Where:** `lib/corp_actions.py` `_ib_unsupported_events`.
- **Current behavior:** an IB merger paying shares AND cash (`WITH <id> 1 for 2 AND USD 5.00`), or any other merger row the parser does not recognise, becomes an `unsupported` corporate-action event: `taxjson run` stops (exit 3) naming it until the exchange is recorded by hand in a `.tt` file and the event is elected `ignore`. Cash takeovers (`FOR USD 30.00 PER SHARE`) are booked as sales; share-for-share mergers (including decimal ratios and class-share tickers) go through the merger election.

### Carryover has no inclusion-rate adjustment for pre-2001 losses
- **Where:** `bin/taxjson_carryover.py`.
- **Current behavior:** the ledger is at 100% with the 50% rate applied on the T1A, correct for post-2000 losses; a pre-2001 net capital loss (¾ or ⅔ rate) fed in via `--claimed` is not rescaled per s.111(1.1). (The cancelled 2024 two-thirds proposal was never applied anywhere.)

### `days_held` uses trade dates
- **Where:** `lib/core.py` closing branch.
- **Current behavior:** the days-held figure counts from trade dates while every other Canadian date is settlement-basis. Canada has no holding-period rule, but the figure also gives the year of acquisition `form-export` prints on Schedule 3 (disposition date minus days held), so a lot bought on a late-December trade date that settled in January shows the earlier year. `taxjson tax-logic` states it (CA-DISP-07, audit A2-0962).

### Non-eligible dividends are estimated as eligible
- **Where:** `src/taxjson/lib/tax_estimate.py` `estimate_canada`.
- **Current behavior:** every Canadian-source dividend gets the eligible gross-up (38%) and credit. Non-eligible dividends (small-business corporations, some REIT/LP distributions: 15% gross-up, smaller credit) are taxed HIGHER than that, so the estimate understates them. T5 box 18 capital-gains dividends (split-share and mutual-fund corporations) are no longer part of this: name them in `capital_gains_dividends.map` (README) and the estimate taxes them as capital gains (audit R1-62, S023-08). T3 trust allocations (interest, ROC, capital gains) are not split by type at all.
- **Why deferred:** brokers' activity exports do not carry the T5 box; the split is only known from the slip.
- **Workaround:** the estimate is disclosed as an estimate; use the T5/T3 slips for the return. (`taxjson reconcile-slips` reads only T5008 / 1099-B disposition slips; it does not check dividend slips.)

### reconcile-slips cannot read per-type-code T5008s or scope a slip to one broker
- **Where:** `src/taxjson/bin/taxjson_reconcile_slips.py`.
- **Current behavior:** comparison is per security. IBKR issues one T5008 row per type code (SHS / OPC / WTS / FUT) identified as "Various"; such a slip cannot be compared, so transcribe a per-security CSV. Several brokers' slips are reconciled together against the account's combined dispositions; a single broker's slip cannot be scoped to that broker's own sales when one account mixes brokers (the gains rows carry no broker).
- **Why deferred:** a per-type-code total mode needs each disposition's broker (to separate IB's sales from Webull's and RBC's in a mixed account) and a warrant/share split the books do not carry.

## Latent assumptions (audit-flagged, not firing on current data)

### Currency⇒exchange suffix map is duplicated in ~6 places
- **Where:** `base.py` (`BaseBrokerage.CURRENCY_EXT_MAP`), `corp_actions.py` (`_CURRENCY_SUFFIX`), `ib_extractor.py` (`_IB_CURRENCY_EXT`, plus the ISIN-country map `_ISIN_EXT`) and a partial copy in `webull.py` (`CURRENCY_EXT_MAP`) each hardcode `{'CAD':'TO','USD':'US','AUD':'AX','GBP':'L'}`; `price_chain.py` and `bin/taxjson_t1135.py` carry reverse/extended variants (suffix→currency, suffix→country).
- **Risk:** a non-G4-currency listing (EUR/CHF/JPY/…) or a USD security on a non-US exchange gets the wrong suffix, splitting/merging ACB pools; and the copies can drift when one is changed. The IB `IE→L` item above is one instance of this broader pattern. An explicit `.TO` in a Questrade or generic export is kept whatever the row currency (`DLR.U.TO` bought in USD), so the known USD-on-TSX case no longer depends on the map. Fix: centralize the map in one helper. (Note: `ticker_map.map_ticker`'s blanket US→TO remap is only used in `generate_summary`, a diagnostic — **not** the live `apply_mapping` transaction path — so it does not silently merge real pools.)

### Sub-micro quantity tolerance in the US engine (crypto dust)
- **Where:** `lib/core.py` (US engine) — no row or lot below 1e-8 units is booked.
- **Current behavior:** a US row under 1e-8 units is left out and named in a warning. (The Canada pool walk keeps a coin residue of any size with its cost — only float noise, under 1e-11 of the position, drains; share pools keep the 1e-6 tolerance: tax-logic CA-CRYPTO-09.)
- **Why deferred:** the US lot epsilon also absorbs float noise in every FIFO lot split; a per-asset tolerance there needs its own fuzz audit. Effect: cents.

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
- **Which one do I file from?** **`<account>_wash.sum` is canonical** for the gains and the wash treatment: it includes the full cross-account wash treatment. `<account>.sum` is the pre-comparison baseline. Its TOTAL PROCEEDS / TOTAL COST lines are the engine's signed figures (short covers and written-option buy-backs count as negative proceeds), not Schedule 3 proceeds/ACB — take those from `taxjson form-export` (or the FOR THE RETURN block of `taxjson sum`).
- **Why not collapse them:** the pre/post comparison is the design's value-add. Future change candidate: bake the "POST-WASH (FILE FROM THIS)" / "PRE-WASH (DIAGNOSTIC)" label into a header line at the top of each file so the role is unambiguous when a user opens one in isolation.

---

### T1135 cost amounts follow the books — custody transfer-ins carry only declared cost
- **Where:** `src/taxjson/bin/taxjson_t1135.py` (`TRANSFER` in `_NON_CAPITAL`; taxable books post-sidecar contain no TRANSFER rows at all).
- **Current behavior:** a position established by a custody transfer-in contributes to the T1135 cost-amount threshold only through whatever acquisition history the books carry (imported buys, `start_pos`/backdated `.tt` declarations). A transferred-in foreign position with lost history listed in `phantoms.json` shows as a phantom opening (`taxjson t1135` applies the project's phantoms.json the way the gains stage does, and flags a still-held phantom "cost understated") — the threshold test can understate until the true history is declared.
- **Why this is the design:** T1135 cost amount IS adjusted cost base; the tool refuses to invent one from a transfer's arrival market value. Declare the real history (the same `custody_fixes.tt` pattern the wash engine prescribes) and the threshold is right.

### T1135 sees only the brokerage books
- **Where:** `src/taxjson/bin/taxjson_t1135.py` (`build_report`, `render_report`).
- **Current behavior:** the $100,000 test sums the cost of the foreign property in the taxable accounts' books. Specified foreign property held outside them — a foreign bank account or cash, shares held in certificate form, foreign real estate or a debt owed by a non-resident — counts toward the same threshold at the same time (ITA 233.3) and is not seen. The report says so beside its verdict ("on these books"), and the verdict is provisional ("so far") until the year has ended. A long option the books still hold after its expiry date is counted at cost and named.
- **Why deferred:** the books carry no such property, and its cost on each day of the year (the test is on the simultaneous total) needs an input of its own. Sketch: a `[t1135] other_property = [{cost, from, to, country}]` table added to the walk's daily total and the per-country table.

### Futures are booked on their settled P/L (both countries)
- **Where:** `src/taxjson/lib/futures.py` (applied by convert-currency / merge2 with the project's `--country`; it used to key on a CAD target), `core._trade_money` and the US engine's `tx_net`, `taxjson_form_export.build_schedule3`.
- **Current behavior:** a plain futures fill that opens a position carries no money, and a close carries the realized native P/L (average cost, commissions on both legs), converted at the closing leg's rate. So the per-account `.sum` shows COST 0 and PROCEEDS = the P/L for futures, `list`/holdings show an open futures position at ACB 0, and Schedule 3 line 6 shows a gain as proceeds and a loss as ACB. The P/L is realized at the close, not marked to market daily; converting each day's variation margin at that day's rate would differ by about P/L x the FX move over the holding period. Blended pools across accounts settle each account's futures separately. A futures row other than a BUYSELL fill (an OPENING_BALANCE, a transfer) stops the conversion with the row named. US books use the same settlement basis, with a partial close taken FIFO from the open contracts (Canada: average cost); §1256 marking and 60/40 are still not modelled (see above).
- **Why this is the design:** only the variation margin changes hands; the notional is never paid (ITA s.261(2)(b) converts amounts that arise), and the broker's T5008 reports futures the same way (cost 0, proceeds = P/L).

## CLI silent-fail conditions

### `to_base_curr.py` real-time intra-day fetch
- **Where:** `src/taxjson/bin/to_base_curr.py` (the intraday real-time block at the end of main).
- **Current behavior:** if the intra-day spot fetch from yfinance fails, the failure is swallowed without a stderr message. The historical rates already emitted are unaffected.
- **Why deferred:** the most common failure mode is "market closed" (weekends, holidays, evenings). Logging on every off-market run would be steady noise drowning out real warnings.
- **Workaround:** the historical-rates path emits its own loud `WARNING:` line on a fetch failure, so a real outage is still visible — only the optional intra-day stamp is silent.

---

## Known engine corner cases (latent — not on the standard `taxjson run` path)

These are real bugs in code paths the standard `taxjson run` flow never exercises. They're documented so anyone repurposing the engine knows.

### US: a move between two of your own taxable accounts does not carry the lot
- **Where:** `taxjson run` with `transfers = false` (the default) in a US project.
- **Current behaviour:** a security moved from one of your taxable accounts to another keeps its basis and purchase date (the move is not a sale), but the US books keep FIFO lots per account and the move's TRANSFER rows sit in the transfer sidecar, so the receiving account's sale of those shares reads as a short with no basis and the sending account still holds them. Since the re-audit (A2-0032) the run prints an `ATTENTION` line naming each such move (paired out/in legs of one symbol and quantity within 10 days) and `run --strict` stops; report those sales by hand. Canada pools the ACB across the accounts (s.47), so it is not affected.
- **Fix sketch:** for each paired move, replay the sender's FIFO lots up to the move date, hand the consumed lots (date, cost) to the receiver as carried lots, and remove them from the sender without a disposition (a lot-transfer row both US engines understand), then drop the ATTENTION.

### RESP accounts are treated as affiliated for the superficial-loss rule
- **Where:** every account with `type = "sheltered"` is an affiliated person in `lib/core.py`'s wash pass.
- **Question:** s.251.1(1)(g) affiliates a trust with its majority-interest beneficiary. CRA's T4037 treats an RRSP or TFSA as affiliated with its annuitant/holder, but an RESP subscriber is usually not a beneficiary, so whether an RESP purchase can deny the subscriber's loss is not settled.
- **Current behaviour:** conservative — an RESP purchase inside the window that is still held at its end denies the loss (permanently, as for any registered account). A filer who takes the other position has to adjust by hand; the 2026-09 audit found one such case on real books.

### Foreign return of capital is only reclassified for IBKR (Canada projects)
- **Where:** in a Canada project, `lib/brokerages/ib_extractor.py` treats a "(Return of Capital)" distribution from a non-Canadian ISIN as a dividend (ITA s.90(1)) and a payment in lieu as income; a US project (and `taxjson-brokerage` without `--country canada`) keeps every return of capital as a basis reduction. Questrade and RBC exports carry no ISIN, and a `.US` listing does not prove a foreign issuer, so their ROC rows stay ACB reductions — check US-issuer ROC on those brokers by hand.

### A merger's per-account empirical ratios are blended
- **Where:** `lib/core.py` folds one merger's rename SPLITs with different per-account ratios into a single holdings-weighted ratio (2026-09). Totals and the shared ACB pool are right; each account's wash-walk balance can be a fraction of a share off.

### Payments in lieu: what the exports cannot say
- **Where:** `lib/income_dating.py` (`pil_is_dividend`), `lib/brokerages/ib_extractor.py`, `rbc_direct.py`.
- **Current behaviour:** in a Canada project a payment in lieu on a Canadian issuer's share paid by a Canadian dealer is a taxable (eligible) dividend (ITA s.260; tax-logic CA-INC-03); the dealer comes from the IB statement's BrokerName ("Interactive Brokers Canada Inc."). An IB file without that header row leaves the dealer unknown and the payment ordinary income. A payment in lieu on a Canadian TRUST unit is trust income under s.260(5.1)(b), not a dividend; the exports do not say which issuers are trusts, so it is counted as a dividend. RBC books its "CASH IN LIEU OF DIVIDEND" rows as plain dividends (RBC is a Canadian dealer, so the Canadian-issuer case is right; a foreign issuer's is a foreign dividend rather than other income).
- **Workaround:** the dealer's T5 (box 24 and the other income boxes) is authoritative; compare with `divs-sum` / `dil-sum`.

### US January fund and REIT dividends need a list
- **Where:** `lib/income_dating.py`; tax-logic US-INC-DATE-RIC.
- **Current behaviour:** §852(b)(7) / §857(b)(9) put a fund or REIT dividend declared in October–December and paid in January on Dec 31, but no export says which payer is a fund. A US project keeps the pay date, warns when a January dividend has an October–December ex date (IB accruals, from any statement of the same IB account and matched to a posting within a week of the accrued pay date) or record date (Questrade/RBC), and moves the payments in `[settings] ric_january_dividends` to Dec 31.

### `wash-sales --explain` traces each account on its own
- The explain trace predates the blended passes; the numbers in the table are the blended ones.

## Conventions

- **Severity ranking:** items above are loosely ordered: gaps that drop tax-relevant data first, cosmetic / latent items last.
- **What counts as "fixed":** the item is removed from this file *and* a regression test pins the corrected behavior.
- **What counts as a "workaround":** any user-side action that makes the bug not fire on their data (manual edit, `ticker.map` override, splitting input files, etc.).
- **What does NOT belong here:** items that are simply unimplemented features (e.g. a brokerage we haven't written a parser for) — those go in `CONTRIBUTING.md` or roadmap docs.

---

## Graduated (fixed)

- **Option premium timing across a year end (ITA s.49(1); IT-479R paras 23–32)** — 2026-09: a written option's premium is now a gain in the year written under `option_premium_timing = "grant"` (Canada default), a buy-back a loss in its own year, an assignment folded with no grant record; `taxjson option-boundary` names any filed year to amend. Previously the premium was recognised at the close (the US §1234 convention).
- **Income dating and payments in lieu (partition Phase C, 2026-09-30)** — a Canadian trust's distribution is now income of its record-date year and its return of capital lowers the ACB on the record date (Questrade/RBC); a Canadian dealer's payment in lieu on a Canadian issuer's share is a dividend (s.260); US January fund/REIT dividends are warned about and can be listed. Previously every row was dated by its pay date and every payment in lieu was ordinary income.
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
`Transaction Fees` (UK Stamp Tax) are a breakdown of the trade's
Comm/Fee, which already includes them (the Cash Report shows
Commissions + Transaction Fees = the Comm/Fee sum) — the fold this
item first shipped charged them twice and was removed in the 2026-09
parse hardening; `Commission Adjustments` refunds
are folded into the trade they name (a lower cost for a purchase,
higher proceeds for a sale; tax-logic CA-ACB-COMMREFUND) — in the
same statement or in another statement of the account (a December trade
refunded in January), and a refund naming one execution of an Order row
folds into that order; a refund that matches no trade, or several, is
kept as a negative FEE row, with a note; tender / voluntary-offer journals are netted
(zero-proceeds round trip = recognized no-op, cash settlement = a
booked sale with a NOTE; an allocation that delivers ANOTHER security
is an UNBOOKED warning — book the exchange by hand). Kraken `transfer/transferpeertopeer` is
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
pricing. Kraken Earn allocation/deallocation shuffles (paired rows)
remain ignored — internal moves; a `hybridearnwithdrawal` row has no
counter-leg and is custody evidence like a withdrawal (a TRANSFER in the
sidecar). A Kraken coin fee on a withdrawal/deposit is named in the
TRANSFER's description (`withdrawal (fee 0.002 TAO)`) and booked as its
own sale of the fee coin (CA-CRYPTO-03), never in the row's money `fee`
field. Tax semantics still not assumed: only the
user knows gift vs self-custody move; genuine gifts are declared as
`.tt` sells at FMV. Pinned by tests/test_transfer_sidecar.py
(TestCryptoSendsBecomeEvidence). Since then `taxjson crypto-sends`
pairs each send with its arrival on another exchange, asks about the
rest (self / gift / payment, saved in `inputs/<acct>/sends.json`) and
generates the FMV sells into `inputs/<acct>/crypto_sends.tt`
(tests/test_fix_sends.py); a matched send that arrived short with no
fee stated (a Coinbase Send) books the shortfall there as the network
fee, a sale at fair value (2026-10, audit R1-26). Limits: pairing reads the crypto accounts'
sidecars only (a send to an equity or `transfers = true` account looks
unmatched); the stablecoin pool is rebuilt from Kraken ledgers and
Coinbase exports (a Kraken trades export without its ledger is not
read for it) and does not add a superficial loss back into the pool's
cost.

- **2026-09 (crypto blended pass):** a Canadian project with two or more taxable `crypto = true` accounts now runs ONE blended crypto pass as well (s.47 averaging and the superficial-loss rule across exchanges); each exchange's book was computed alone before, while the run's overlap note claimed the blend covered them. `audit` and `check-filed` recompute the same way; US crypto (no §1091) stays per account. Tests: `tests/test_crypto_blend.py`.
- **2026-08 (blended taxable pass):** the multi-account gap is fixed. One combined gains run now produces the canonical wash-adjusted artifacts for all taxable equity accounts: Canada ACB blends across non-registered accounts (ITA s.47) and US §1091 matches cross-account while FIFO basis stays per account (`taxjson-gains --per-account-basis`); `taxjson-split-gains` rebuilds the per-account files, so every consumer reads its usual filenames. The per-account `<name>.sum` remains the isolated pre-blend baseline (deliberate diagnostic pair). Tests: `tests/test_blended_taxable.py`.
- **2026-08:** `taxjson run --strict` — per-account validation ERRORs promoted to fatal (the non-fatal default remains); `crypto_ticker.map` — user-editable Yahoo-collision overrides for `taxjson-fill-crypto` (built-ins remain as defaults); Coinbase "Convert" rows — two-leg SELL+BUY emission for the recognized `Converted X AAA to Y BBB` Notes pattern (unknown variants still fail hard); Kraken trades-CSV crypto/crypto pairs — two USD-denominated legs mirroring the ledgers path; Kraken legacy concatenated pair formats (`XXBTZUSD`, `XETHXXBT`, `ADAUSD`) — recognized explicitly, unknown shapes refuse loudly; Kraken `_normalize_asset` — X-prefix strip extended to the full enumerated set (XLM/XMR/ZEC/XDG→DOGE/ETC/MLN/REP); Webull blank-`Description` carry-over — reset when the symbol changes; crypto-vs-equity path mismatch — `taxjson run` now refuses a crypto-broker file in an equity account (and vice versa); interactive corp-actions stage — stale `.diag` sidecars are cleared. Regression tests: `tests/test_audit_2026_08_fixes.py::TestKnownIssuesGraduated`.
