# Known Issues and Errata

Known bugs, limitations, and deferred-fix items in taxjson. Each entry describes the current behavior, why it isn't fixed yet, and what evidence would be needed (or what work is required) to address it. Open a PR or attach a sample CSV to graduate any of these.

The codebase has been through eight audit rounds and two full-coverage audits; everything listed here was triaged and deliberately left in place rather than overlooked.

---

## Brokerage parser limitations

### Positions reports: what is not read
- **Where:** `src/taxjson/lib/positions_reports.py` (`taxjson opening`, `taxjson sanity`).
- **Current behavior:** an RBC Holdings Export is read by its column LABELS (Symbol, Quantity, Currency, a book cost / book value or average cost column, Market Value, Account): no real export was available to pin its exact layout. IB's Open Positions **Lot** rows are not read (no layout with acquisition dates is documented), so a US opening from an IB statement has no lot dates and is refused: list the lots in a holdings TOML (`acquired = ...`). Questrade, Webull, Coinbase and Kraken have no positions export the parsers know (Questrade: `taxjson fetch --positions` writes a holdings TOML).
- **What would graduate it:** a real (redacted) RBC Holdings Export and an IB statement with Lot rows.

### Opening balances: edges the cut-off does not see
- **Where:** `src/taxjson/lib/opening.py` (merge2 stage).
- **Current behavior:** the snapshot cut-off runs on the merged rows, so a `[[distributions]]` adjustment (added after merge2) and a missing-history opening from a `.tt` `OPENING ... cost=unknown` line (added at the gains stage) dated before a snapshot are not left out; one taxjson account holding the same symbol at two brokers with different snapshot dates cannot be opened per broker (one snapshot date per symbol per account). A US project asks for a lot date on every OPENING line, a retirement account's included (where the holding period does not matter).
- **Why deferred:** each needs the account type or the broker account on the row at the merge stage; none is in the owner's books.

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
- **Where:** `src/taxjson/lib/brokerages/ib_extractor.py` — the module-level `_ISIN_EXT` map (`'IE': 'L'`), read through `_isin_ext()` by the Dividends and Withholding Tax branches (the Corporate Actions and Transfers branches derive suffixes via `_ib_listing_ext` instead: the currency's suffix, with a TSX `.U` unit kept on `.TO` and an LSE-venue USD line on `.L`).
- **Current behavior:** every Irish-domiciled (ISIN prefix `IE`) security is mapped to a `.L` (LSE) market suffix. Partially mitigated since the income-reattribution pass: DIVIDEND / DIVIDEND_IN_LIEU / TAX rows are re-bound to the suffix of the position actually held for that ticker in the statement (`_reattribute_income_to_holdings`; when the ticker is held under two listings during the statement, the one held on the payment date), so income no longer lands on a spurious `.L` symbol when the shares are held under another suffix. Since 2026-09 the holding may come from any of the account's IB statements (a statement with only a dividend row), and the rebind requires the held listing's ISIN (Financial Instrument Information) to match the income row's — a different issuer sharing the ticker keeps its own listing.
- **Why deferred:** it fires only for IE-domiciled ETFs, which are uncommon in a Canadian retail account. Most IE-domiciled ETFs trade in EUR / multiple currencies, not all on LSE; a real fix needs an ISIN → exchange lookup or a per-ticker override.
- **Workaround:** users who hold IE-domiciled ETFs should add a `ticker.map` GLOBAL rule rewriting the parsed `.L` symbol to the correct market suffix.

### IB cash-in-lieu row wording is unverified
- **Where:** `src/taxjson/lib/brokerages/ib_extractor.py` — `_IB_CIL_RE` in the Corporate Actions branch.
- **Current behavior:** a Corporate Actions row whose description contains the phrase `cash in lieu` (any case, anywhere after the leading `TICKER(ISIN)` token) with a negative Quantity is booked as a sale of that fractional quantity for the row's Proceeds (a blank Proceeds is refused; a 0 is booked as 0 with a warning — IB's Value is a market value, never cash), and the fraction is folded into the leg-derived ratio of the same symbol's split nearest its date, within a week (in either row order), so the pool ends on whole shares; a fraction no split claims is said in a note. The regex was written against a synthesized row (`TINY(US…) Cash in Lieu of Fractional Shares (TINY, TINY CORP, US…)`, quantity `-0.3333`, proceeds `3.10`) — no real IB statement with such a row was available.
- **Risk:** if IB words the row differently (no `cash in lieu` phrase, or the fraction/cash in other columns), the row falls into the unhandled corporate-actions tally (warned at end of parse) and the fractional dust stays in the pool — the pre-fix behavior, not a silent mis-booking.
- **Evidence needed:** a real IB Activity Statement CSV containing a reverse split (or merger) with its cash-in-lieu row; attach it to graduate this item.

### IB `Trades / Forex` conversions are not in the position books
- **Where:** `src/taxjson/lib/brokerages/ib_extractor.py` — the Trades branch, asset category `Forex` (rows like `Trades,Data,Order,Forex,CAD,<account>,USD.CAD,"…",<qty>,<T. Price>,…`), `_book_forex`, `ib_cash_events`.
- **Current behavior:** an explicit currency conversion is COUNTED as a recognized non-event of the position book (the calmer `taxjson-brokerage` note: `Trades/Forex (currency conversion, not modeled — KNOWN_ISSUES)`) and booked for the Cash Report reconciliation only. No fake `USD` / `CASH.USD` asset is emitted — doing so would put a fake position in the book. The FX-on-cash ledger v2 (opt-in, `fx_cash_ledger = "v2"`) reads each Forex order as a conversion at its actual amounts, with the `Deposits & Withdrawals` rows and the Cash Report's Starting/Ending Cash (`ib_cash_events`); the default ledger does not, which is why every default fx-cash output says NOT RELIABLE.
- **Work needed:** v2 is under audit and stays opt-in until it has been checked on real books (see "FX on foreign cash: ledger v2 limits").

### fx-cash: which corporate-action rows move cash
- **Where:** `src/taxjson/bin/taxjson_fx_cash.py` — `_non_cash`.
- **Current behavior:** rows emitted by the corp-actions stage (`corp_event_id` set: share-for-share mergers, taxable exchanges at FMV, spin-off ACB allocations) move no foreign cash and are left out of the s.39(1.1) ledger; so are crypto-for-crypto legs (Kraken swaps, Coinbase Convert and Advanced Trade on a crypto-quoted pair), Kraken fees paid in a coin, and staking rewards paid in a coin (a reward in any USD stablecoin, PYUSD and GUSD included, is US-dollar cash in a Canada book). The cash an event paid — cash in lieu (a standalone leg, or folded into a taxable exchange's proceeds), the snapped fraction of a spin-off, §356 boot — is ledgered from the row's `corp_cash` field (re-audit A2-1014). A cash takeover is a sale the broker parser books and is ledgered normally.

### FX on foreign cash: ledger v2 limits
- **Where:** `src/taxjson/lib/fx_cash_v2.py` — `build`; `src/taxjson/lib/cash_events.py` — `collect`.
- **Current behavior:** the default ledger (v1) is flagged NOT RELIABLE everywhere and never shows a reportable figure. The opt-in ledger v2 refuses (NOT COMPUTED, each problem listed) rather than guess, which on real books means it needs `.tt` lines for what no export carries: a `CASHOPEN` for the first year it is used (later years carry the pool `close-year` records), a `CASHMOVE` declaring each deposit and withdrawal (or `fx_cash_inflow_cost = "spot"` for inflows), and a `CASHBAL` for every account whose export has no balance (RBC, Webull, Coinbase, a bank). It reads Questrade's FX conversions (`FXT`), deposits, withdrawals, cash-only transfers and stock-lending income, but no Webull or generic-importer cash event: such an account that moves foreign cash is refused until its `.tt` cash lines are declared complete (`CASHBOOK <book> complete`); a Questrade `BRW` cash journal is not read. IB's daily cash settlement of futures ("Cash Settling MTM") is not modelled, so an IB account holding futures at a statement end does not reconcile; a deposit advance and its cancellation are netted (the deposit counts on its own date); a combined IB statement is one cash account — its Cash Report has no Account column, so a debt in one of its accounts and cash in another net out (the ledger says so in a note; the work needed for a per-account split: the IB parser stamping each row's `Account` on the books' rows, the cash events carrying it, and the combined Cash Report checked against the sum of the accounts' books); a `.tt` file in a folder with several broker accounts needs a `CASHBOOK` line (never guessed from its name), so a file mixing several accounts' rows is split by hand.
- **Why opt-in:** the owner's instruction — not on by default until it has been scrutinized and audited.

### Kraken fiat conversions are not modeled
- **Where:** `src/taxjson/lib/brokerages/kraken.py` — `_parse_trades` (a fill whose BASE is fiat after stablecoin folding: `USD/CAD`, `USDC/USD`, `USDT/CAD`) and `_build_instant_trade` (a `spend`/`receive` pair whose both legs are fiat: USDC dust swept to USD, USD → CAD).
- **Current behavior:** counted as recognized non-events (`forex conversion … not modeled — KNOWN_ISSUES`). Previously each emitted a BUYSELL of a fake `USD` / `CAD` asset (the fiat base treated as the traded security), which put a fake position in the crypto book and a nonsense trade in the gains report.
- **FX on cash:** foreign-cash gains live in `taxjson fx-cash`. Its default ledger reads no conversion row (it says NOT RELIABLE); the opt-in ledger v2 reads a Kraken ledger export's fiat-for-fiat trades, fiat deposits and withdrawals and its running balances (`kraken_cash_events`), and Coinbase's stablecoin buys and sells for fiat (`coinbase_cash_events`). Stablecoin↔USD swaps are a wash by construction (folded 1:1 for pricing) — in a Canada project; a fill more than 2% off 1.00 USD prints a de-peg warning.
- **US projects differ:** every USD stablecoin (the market-data list and ticker.map `STABLE` lines) is property there, on Kraken and Coinbase alike (tax-logic US-CRYPTO-02): `taxjson run` parses with `--country usa`, so a `USDC/USD` fill, a `Buy`/`Sell USDC` row, a swap against a stablecoin, a stablecoin reward or fee are booked as purchases and sales of the coin (a swap, reward or fee at the 1.00 USD par; a sale for dollars at its price). Kraken ledger-only instant trades between a stablecoin and dollars follow the same rule.
- **Coinbase follows the same model:** `Buy USDC` / `Sell USDC` rows are counted as stablecoin conversions (non-events) and the USDC leg of an Advanced Trade on a `*-USDC` pair is cash, not a position. An Advanced Trade on a crypto-quoted pair (`ETH-BTC`) is a swap: the quote coin's leg is booked too, at the fill's stated value (2026-09 audit R1-102). Strictly (CRA) a stablecoin is a crypto-asset, so the USD/CAD movement while USDC is held is an unbooked gain/loss — a few dollars a year on real data.

### Canadian listings carry no venue (`ROOT.TO` for TSX, TSXV, CSE and NEO)
- **Where:** `src/taxjson/lib/brokerages/base.py` — `canonical_ca_listing`, used by `apply_currency_suffix` (Questrade, RBC, Webull, generic) and the taxjson-fetch plugin's `taxjson_fetch.api.qt_position_symbol`; IB stamps every CAD listing `.TO`.
- **Current behavior:** one Canadian security has one symbol whichever broker reports it: `ROOT.TO`, with a TSX preferred series dotted (`FTN.PR.A.TO`). RBC and Webull exports do not name the venue, and real books (ticker.map `TOBASE` rules) are keyed on `.TO`, so the venue suffixes `.V` / `.CN` / `.NE` are not used as identities. A `.tt` line is read the same way (`ABC.V` on a CAD line, `ABC.VN`, `FTN.PRA.TO` become `ABC.TO` / `FTN.PR.A.TO`). In a Canadian project a ticker.map or tobase.map `TOBASE` or `DISTINCT` line that writes `ROOT.V` also covers `ROOT.TO` (tax-logic CA-XLIST-06); a `GLOBAL` line that writes `ROOT.V` still splits the pool from `ROOT.TO`, and so does any `.V` line in a US project; `taxjson-lint-crosslistings` flags it (CANADIAN VENUE SPLIT), `.VN` and undotted preferred series included.
- **Why this is the choice:** the alternative (venue suffixes everywhere) needs every parser to know the venue; RBC and Webull cannot, and IB would rename a holder's Venture/CSE positions. TSX and TSX Venture share one symbol namespace, so `.TO` is unambiguous for Venture names; a CSE/NEO ticker that duplicates a different TSX ticker would share a pool (as it already did in IB statements).
- **Workaround:** price lookups that need the venue use a ticker.map `QUOTE` line (`QUOTE SAMPLY.TO SAMPLY.V`).

### Trade reversals across export files
- **Where:** `src/taxjson/lib/trade_cancel.py` (IB `Ca`), `src/taxjson/lib/brokerages/questrade.py:_pair_reversals` (CIL / REI / stock dividend).
- **Current behavior:** an IB cancellation pairs with its original in the same statement or, through `taxjson-merge2`, in another statement of the same account; with no original anywhere it stays booked as a reversing trade and merge2 warns. An OVERLAPPING statement of the same IB account (a download taken before IB posted the cancellation) that still holds the original drops it too, for Trades, Transfers and Corporate Actions rows, so dedup keeps one book (`IbBrokerage.reconcile_files`); statements of different IB accounts (Account Information) never touch each other. When a later statement cancels a row of an EARLIER statement and rebooks it (both dated before the later statement's period), the cancellation removes the earlier statement's original and the rebook is booked; it pairs with the rebook only when no statement of the account holds the original. A Questrade CIL/REI/stock-dividend reversal (and an RBC REI CANCEL) pairs with its original in any export of the same account, overlapping copies included; with the original in no export of the account the parse is refused.
- **Why deferred:** no real Questrade reversal row has been seen, so its cross-file shape (same code, negated signs, later date) is inferred from how Questrade reverses dividends.
- **Workaround:** when the original is in no export (an export window that starts after it), book the correction in a `.tt` file.

### A negative futures price in a generic or `.tt` file
- **Where:** `src/taxjson/lib/brokerages/generic.py` (`_trade_net` takes the magnitude); `src/taxjson/bin/taxjson_convert_tt.py`.
- **Current behavior:** IB futures rows keep the sign of a negative price (audit A2-0092). A generic-import futures row at a negative price is still read as its magnitude, so its P/L sign is wrong. A `.tt` line keeps a negative price and total as written, so it is right when the total carries the sign; a `.tt` line that writes a negative price with a positive total is not caught (the qty x price check runs only for a positive price).
- **Workaround:** book such a fill from the IB statement or as a `.tt` line with the signed total, or enter the realized P/L of the close by hand.

### Identical rows in two exports with little overlap are booked once
- **Where:** `src/taxjson/bin/taxjson_sort.py` — `plan_dedup`, used by `taxjson-merge2 --dedup`, `taxjson-sort --dedup` and `fees-sum`.
- **Current behavior:** the same row in two exports of one account is booked once. Sometimes the files' overlap cannot show that they are copies of one export: they share only that row, or each holds rows the other lacks on the dates both cover, or a `.tt` line equals an exported row. The row is still booked once, as a re-export, and `taxjson run` prints `Warning: ATTENTION: dedup: ...` naming both files and the row. Exports are cut by date, so two exports of ONE broker account can only both hold one fill when they both cover its whole day, and then both files hold all of that day's fills; two separate identical trades of one account can only end up split across files when an export is cut up by hand. Rows of two different broker accounts are never collapsed: every parser that reads the account (IB, Questrade, RBC, Webull, a generic mapping that names it) stamps it on each row. Identical lines in two `.tt` files are both booked. Kraken and Coinbase exports name no account; their rows carry the exchange's own transaction id, so identical rows do not arise.
- **Why this is the choice:** an export carries no row id. Booking such a row twice would double-count the common case, a boundary day that both exports include.
- **Workaround:** if the ATTENTION line names two separate trades, enter the second one as a `.tt` line.

### Kraken fees taken in the traded coin are not in the fee reports
- **Where:** `src/taxjson/lib/brokerages/kraken.py` — `_parse_trades` (a fill whose ledger shows the fee taken in the base coin) and the ledger instant-trade path (a crypto leg's fee).
- **Current behavior:** the fee coins are folded into the quantity (fewer coins received on a buy, more given on a sale) and the fill's `fee` field is 0, so cost basis and proceeds are right, but `fees.rpt`, `taxjson fees-sum` and the `.sum` FEES line leave these fees out (they can be most of a Kraken account's trading fees). The parse note says so.
- **Why deferred:** the `fee` field feeds the engine's per-row fee figures; recording a fee already inside the quantity there needs an informational-only fee field first.

### Questrade `commission` vs everyone else `fee`
- **Where:** `src/taxjson/lib/brokerages/questrade.py`.
- **Current behavior:** Questrade transactions emit a `commission` key; IB / RBC / Webull / Kraken / Coinbase all emit `fee`.
- **Why this isn't a bug:** cost-basis math is correct because the engine works from `net_amount`, which already includes the charge; the fee reports sum both fields (`taxjson_fees.py`), and `core.py`'s display-only `_effective_fee_for_trace` uses `tx.commission + tx.fee`. The inconsistency is cosmetic — per-row reports that itemize one column show the values under different headers across brokerages.
- **Why deferred:** pure refactor with no behavioral change. Touching every test and downstream consumer for a cosmetic split isn't worth the churn.

---

## Cross-parser asymmetries (from the 2026-06 flow audit)

Capabilities one broker parser has that a comparable one lacks. The ones below are deferred because they need a real broker sample to implement safely, or are a design decision.

### Webull exercise/assignment inference
- **Where:** `src/taxjson/lib/brokerages/webull.py` — `_mark_assignments`.
- **Current behavior:** Webull's Trading Summary shows an exercise or assignment only as a $0 option close plus an ordinary stock trade at the strike. The parser pairs them (both legs ASSIGN, premium folded into the shares' cost or proceeds — in Canada s.49(3) for a call, s.49(3.1) for a put; the note cites the project's own law: Rev. Rul. 78-182 in a US project) when the stock trade is on the same underlying (the row's own `@Symbol`), for 100 x contracts shares in the matching direction, at the strike, settling -1..+7 days from the close, AND carries the account's configured exercise/assignment charge (`[accounts.<name>] exercise_fee`, e.g. 1.00; with no `exercise_fee` nothing is inferred and every such pair is named as a candidate); the smallest settle gap wins across every option, and exports beside the file are searched too (a Dec-31 assignment whose shares settle in January). Every inferred pair is named on stderr. A trade at the strike with an ordinary commission is NOT paired (a limit order at a round strike after a worthless expiry) and is named as a warning instead; so is a $1.00-charge trade whose quantity does not match the close one-to-one (2 contracts closed against two 100-share rows, or two 1-contract closes against one 200-share row) — book that one by hand. A paired option leg settles with its stock leg (CA-DATE-04 / US-DATE-04). A $0 option row is accepted only as a close: at the expiry, or earlier as a paired exercise/assignment (an unpaired early $0 close is named); a $0 row that opens a position, and any share row with no Price and no Proceeds, is refused. Exports of a different Webull broker account (the preamble's Account Number) are never paired with each other.
- **Why this is the choice:** the export has no action code for exercise/assignment; the broker's exercise charge is the only evidence that separates a real one from a coincidental trade. taxjson does not assume the charge (a fee schedule changes): you state it per account. A real assignment whose charge differs from the setting is booked as an expiry plus a trade, with the warning naming it.

### Webull Trading Summary carries no income
- **Where:** `src/taxjson/lib/brokerages/webull.py` — the Trading Summary holds BUY/SELL rows only.
- **Current behavior:** Webull interest and dividends (T5 slips) are not in any Webull input, so the account's income summary leaves them out. A row with another action code (DIV, a transfer) that does appear in a Trading Summary is reported as `warning: UNBOOKED:` (echoed by `taxjson run` as `Warning: UNBOOKED:`, refused by `--strict`). Enter them by hand in a `.tt` file in the account's folder: `INTEREST 2025-12-31 16:00:00 USD 12.34` (T5 box 13; a slip with a blank box 27 is CAD), `DIVIDEND ...` for dividends.
- **Why:** Webull exports no income file the parser could read.

### Questrade dividends with tax withheld are booked at the net amount
- **Where:** `src/taxjson/lib/brokerages/questrade.py` — strips `TAX WITHHELD`/`NON-RES` only as description-key noise; no TAX emission. (Interest rows, `INT`, are booked as INTEREST, sign kept.)
- **Current behavior:** IB and RBC emit dedicated TAX (foreign withholding) records; Questrade's export gives neither the gross nor the tax of a dividend marked NON-RES TAX WITHHELD, so it is booked at the net amount (foreign-tax credit missing, income understated). In a taxable account the parse prints an ATTENTION line listing those dividends.
- **Why deferred:** needs a Questrade CSV that carries the withholding as its own row (or the gross) to parse it correctly.
- **Workaround:** take the gross and the withholding from the T5/NR4 slip.

### RBC identity across projects (one year's export per project)
- **Where:** `src/taxjson/lib/brokerages/rbc_direct.py` (`build_rbc_account_context`).
- **Current behavior:** the RBC parser learns identities from ALL of an account's RBC exports in the project: a symbol's listing, an option code's contract, a temporary reorganization code's company, and overlapping re-downloads of the same RBC account. When a project holds only the current year's export and the earlier years come in through a hand-written `.tt` (`margin_start.tt`), the earlier rows are not there to learn from. What the parser does then: income on a symbol that no file trades keeps the payment currency's listing (a USD return of capital there is an ATTENTION line on the run console with the `TOBASE` line that fixes it); a temporary removal code it cannot name is assumed to be the receipt's ticker, with an ATTENTION line and the `ticker.map` line to fix it; an option that RBC re-describes between years (RCI vs RCI.B, an XCH-adjusted TRP1) keeps the description of this year's rows, so the `.tt` must use the same symbol — a closing row (RBC's `CLOSE CONTRACT`, an expiry, an assignment) the books cannot back is an ATTENTION line on the run console naming the contract held under the other root and the `GLOBAL` line that joins them, and `taxjson handoff` accepts the re-described root (and fails the closed year's root when this year's export closes the other one). A ticker change RBC applied without a reorganization row (ORCC to OBDC) is only an ATTENTION line with a ready `GLOBAL` line, because the export carries no CUSIP to prove the two symbols are one security — and only when an export in the project holds the old symbol's rows: when the old symbol's buys come in only through the start `.tt`, the parser says nothing, and the sign is the run's note that the new symbol goes short (with the old one left open in `taxjson shares`).
- **Workaround:** keep the earlier years' RBC exports in the project's `inputs/<account>/`, or add the suggested `ticker.map` line.

### Transfers into taxable accounts stay out of the books; only a stated book value is booked (by design)
- **Where:** `src/taxjson/lib/brokerages/rbc_direct.py:_build_transfer`, `questrade.py:_parse_transfer` (the `book_value` evidence); `taxjson-brokerage` keeps TRANSFER rows aside unless `--transfers` (`transfers` in `[accounts.<name>]`); `src/taxjson/lib/transfer_in.py` and `taxjson run`'s `stage_transfer_arrivals`.
- **Current behavior:** in a **taxable** account (`transfers` off) the broker's TRANSFER rows stay out of the books, in the transfer sidecar. A transfer-in that no transfer-out of yours cancels (shares from outside your books) is booked as an acquisition on its arrival date at the book value the broker STATES on the row (Questrade "TRANSFER BOOK VALUE", RBC "BOOK VALUE nnn"), said as ATTENTION every run; without one (IB's transfer value is the market value; RBC's Value column is 0) the shares stay out with no cost, said as ATTENTION and counted in the run's closing summary. A `.tt` purchase of the security in the account dated on or before the arrival, or an `OPENING ... cost=unknown` line for it, covers it (no booking, no ATTENTION). Owner decision 2026-10-05 (getting-started study), tax-logic CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV.
- **Limits:** the broker's book value is the sending side's record and may not be your ACB/basis (a superficial-loss denial, a return of capital, the same stock in another account): the ATTENTION says to check it, and the `.tt` line is the override. A US lot booked this way starts its holding period on the arrival date. The arrival is never treated as the purchase in a superficial-loss / wash-sale window (its true acquisition date is unknown). Two unrelated transfer rows of the same security (an out, then an in from another broker) cancel each other.

### IB income rows carry no record date
- **Where:** `lib/brokerages/ib_extractor.py` (the Dividends section has only the pay date); `lib/income_dating.py`.
- **Current behavior:** in a Canada project a Canadian trust's distribution or return of capital is dated by the record date Questrade and RBC print (s.104(13), s.53(2)(h); tax-logic CA-INC-DATE-TRUST / CA-INC-DATE-ROC-TRUST). An IB row has no record date, so a December-record trust distribution IB pays in January stays in the pay year of `divs-sum` and the estimate, and a January-paid IB ROC on a Canadian trust stays on its pay date — the run warns about the ROC with the two `.tt` ADJUST lines that move it to Dec 31.
- **Also:** a Canadian issuer is recognised by its listing (or an IB CA ISIN); the split-share corporations that also say "Distribution" are a short built-in list (`SPLIT_SHARE_ROOTS`) — add any other corporation to `[settings] corporate_distributions`.
- **Why the pay date is kept (owner decision 2026-10-01, audit S057-23):** IB's Dividends section prints only the pay date and labels a trust's distribution a cash dividend, so nothing on the row says the payer is a trust rather than a corporation or split-share issuer (which s.82 dates when paid). The ex date IB's "Change in Dividend Accruals" section gives is kept on the row (`ex_date`, a US project reads it for §852(b)(7)) but is not a record date and does not identify a trust, so a Canada project does not date income by it.
- **Workaround:** compare the TAXABLE line of `divs-sum` with the T3; the slip is authoritative.

### RBC exports by Date miss back-dated year-end book-cost rows
- **Where:** the RBC export window (not the parser: `rbc_direct.py:_build_book_adjust` books the rows correctly when present).
- **Current behavior:** RBC posts year-end book-cost adjustments ("2022 NOTIONAL DISTRIBUTION ADJUSTMENT TO BOOK COST", a year-end ROC) dated Dec 31 but only in the following spring. An export for the calendar year taken before then, and next year's export (which starts Jan 1), both lack them (2026-09 audit R1-85: a loss was understated by the missing adjustment). Since 2026-10 the parse reads each export's "Activity Export as of" stamp: when every export holding the tax year's rows was taken before the account's posting day of the next year (`[accounts.<name>] year_end_posting = "MM-DD"`, default `"06-30"`) and the account held a position at the year end, the account's `.sum` carries a note saying the adjustments may be missing (audit S063-22). The rows themselves cannot be seen until RBC posts them; an export taken before Dec 31 of the year is an ATTENTION on the console, and `checklist` inputs-frozen checks each account's latest RBC export against Jan 31.
- **Workaround:** export each RBC year with an end date after the following June (or re-export the prior year once the T3s are out) and keep the overlapping files: overlapping downloads of one account are de-duplicated row by row.

### RBC notional distributions raise ACB only
- **Where:** `src/taxjson/lib/brokerages/rbc_direct.py:_build_book_adjust`.
- **Current behavior:** a "NOTIONAL DISTRIBUTION ADJUSTMENT TO BOOK COST $x" row becomes an ACB increase (ADJUST `dist`), and the parse warns that the distribution itself is income on the fund's T3 (usually box 21) and is NOT in taxjson's income totals (2026-09 audit S063-17). Stated in tax-logic (CA-DIST-02 / US-DIST-02).
- **Why (owner decision 2026-09-30, D6: stays a warning):** booking it as income needs its character (capital-gain distribution vs other income), which only the T3 gives; booking it as a dividend would gross it up as eligible. Take the amount from the slip.

### Stock dividends: $0 in Canada until the declared amount is added
- **Where:** the parsers emit a neutral stock-dividend event — a $0 BUYSELL of the new shares typed `stock_dividend` — from `questrade.py` (the `DIS` + stock-dividend branch), `ib_extractor.py` (a Corporate Actions `Stock Dividend` row; IB's exact wording is modelled, not seen in a real statement; its `ATTENTION` line shows the row's Value) and `rbc_direct.py:_build_stock_dividend`. Each gains engine applies its country's rule (`lib/core.STOCK_DIVIDEND`).
- **Current behavior:** Canada: the shares enter the pool at $0 cost and count as an acquisition for the superficial-loss rule (tax-logic CA-STKDIV-01); the taxable amount is the fund's *declared* amount, which the CSV does not carry, and the gains run prints a `NOTE:` naming the symbol, date and share count. US: a pro-rata stock dividend is not income (§305(a)) — the new shares join the lots held, the basis is spread over old and new (§307), the purchase dates carry over (§1223(5)), and they are not a §1091 purchase (US-STKDIV-01). A taxable US stock dividend (§305(b)) is not detected. An IB stock dividend whose trailing `(TICKER, NAME, ISIN)` names ANOTHER security (another class) is not booked: an `UNBOOKED` line asks for a hand entry (a split whose new leg names another ticker renames the pool; a cash in lieu naming another security sells that security's fraction).
- **Received while short (both countries, re-audit A2-0495):** a stock dividend paid while the account holds an open short is booked like any $0 purchase, so it covers part of the short at $0 (a gain of the short's per-share proceeds). In fact the short seller owes the lender the new shares — the short position grows and its proceeds are spread over more shares. The US warning then says "no shares held". Workaround: replace the row in a `.tt` with the adjustment you agree with the broker's statement (for example a $0 short sale of the new shares).
- **Impact:** registered accounts — none. Canadian taxable accounts — ACB is understated (gain overstated at sale) until the declared amount is supplied; the zero-basis walk also surfaces the position via `taxjson find-missing-history`.
- **Workaround (the intended flow, Canada):** add the fund's declared per-share amount for the record date as a `[[distributions]]` entry in taxjson.toml; `taxjson run` converts it into the ACB-raising ADJUST. That books the cost side only: the declared amount is also a dividend of the year, reported from the T5/T3 slip — taxjson's income totals, `divs-sum` and `estimate` do not include it (the run's NOTE says so).

### Settlement calendars outside North America skip weekends only; the generic importer keys the market on currency
- **Where:** `src/taxjson/lib/dates.py` (`_T1_CUTOVER`), `src/taxjson/lib/market_calendar.py`, `src/taxjson/lib/brokerages/generic.py`.
- **Current behavior:** the settlement lag follows the market: USD/CAD/MXN T+1 since May 2024, T+2 from 2017-09-05, T+3 before; the UK, the EU markets (every EU currency) and Switzerland T+2 from 2014-10-06 and T+1 from 2027-10-11; the ASX and NZX T+2 from 2016-03-07, Singapore from 2018-12-10, Tokyo from 2019-07-16 (T+3 before each); Hong Kong T+2 throughout; any other market on the North-American dates (not researched market by market; Norway is not keyed to the 2027 EU move). Outside the US and Canada only weekends are skipped — a local bank holiday inside the lag (Jan 1, Boxing Day) is not, so such a settle date can be a day early. The IB parser takes the market from the listing (a USD unit on the TSX settles on the Canadian calendar, a USD line listed on the LSE is `.L` on the UK cycle) and dates ASX, HKEX, Tokyo, Singapore and NZX fills in the exchange's local time (tax-logic CA-DATE-SESSION). Every parser takes the market from the listing suffix the same way (`lib/dates.market_of`; `.L` and `.AX` included, else the row currency); other non-North-American venues (Eurex, the LSE in the evening) keep IB's Eastern clock date.
- **Why deferred:** per-market holiday calendars and venue time zones for markets the books rarely touch.

---
---

## Engine scope (documented out-of-scope rules)

### US: §1091(e)(1) — a long SALE within the window of a short-cover loss is not a wash trigger
- **Where:** `src/taxjson/lib/core.py` US short-side replacement matching (`short_replacements`): only the short-OPEN portion of a SELL (§1091(e)(2), "another short sale") registers as a replacement for a loss on closing a short.
- **Current behavior:** short 100 @100; cover 200 @110 (loss −1,000, opens 100 long); sell 100 @105 two weeks later → the −1,000 cover loss is allowed. §1091(e)(1) ("substantially identical stock ... were **sold**" within the window) would wash it. Same-year totals coincide; cross-year attribution and 8949 code-W reporting can differ.
- **Why deferred:** rare shape (a cover that flips long, then a sale inside the window); documenting the gap is the honest state until a fixture demands it (2026-09 US-engine audit). `taxjson tax-logic` states it (US-WASH-19, audit A2-0062).

### US: a replacement bought and sold in the loss's OWN account before the loss does not wash it
- **Where:** `core.py` US pass — a purchase in the account that sells at the loss replaces it only with the shares still unsold at the loss (FIFO consumption, US-WASH-21).
- **Current behavior:** an IRA purchase (US-WASH-11) and, since the owner's decision on re-audit A2-0544, a purchase in ANOTHER taxable account (US-WASH-22) wash the loss even when sold before it — §1091(a) keys on acquisition in the window, with no still-held test; in the other-account case the disallowed loss is added to the basis of that earlier sale (and booked in the loss's year, with an ATTENTION line, when that sale is in a filed year). A purchase in the SAME account that was sold before the loss shares were bought (buy R, sell R, buy L, sell L at a loss) is still not matched.
- **Why open:** within one account the same reading would chain through every buy/sell cycle (each loss moving into the previous cycle's sale); kept as the documented exception until a case needs it.

### US: options as replacement property are advisory-only
- **Where:** `core.py` `detect_option_replacement_matches` (warn-only in the US engine; the Canada engine enforces the call rule).
- **Current behavior:** §1091(a) covers "a contract or option so to acquire"; a deep-ITM call bought inside the window leaves the stock loss allowed, with a warning. A user policy choice, not a bug — the statute itself is mandatory, so treat the warning as an instruction (2026-09 audit).

### US: specific-lot identification is not supported (FIFO only)
- **Where:** the US engine consumes lots FIFO (Reg. 1.1012-1(c)(1) default). Reg. 1.1012-1(c)(2)–(3) specific identification, and a broker's non-FIFO default (e.g. highest-cost), are not modeled.
- **Consequence:** a broker 1099-B computed under specific ID will not reconcile per-lot; year totals agree only when every lot is eventually sold. Set the broker's lot method to FIFO or reconcile by hand.


### §1256 (60/40 mark-to-market) is not implemented
- **Where:** `src/taxjson/lib/core.py` — documented out-of-scope in the US engine's docstring, alongside the §1233(b)(1)/(2) (long held ≤1 year) and §1233(d) (long held >1 year) short-sale rules and §1259 constructive sales.
- **Current behavior:** futures and broad-based index options (SPX, NDX, futures) are run through the ordinary FIFO ST/LT engine — no year-end mark-to-market, no 60/40 split. The US filing outputs (`form-export --form 8949` / `txf`, `sum` FOR THE RETURN, the close-year lock) recognise them (`lib/futures.section_1256_kind`: plain futures, options on futures, a short list of broad-based index option roots) and keep them OFF Form 8949, listing each with its P/L for Form 6781.
- **Why deferred:** needs a mark-to-market pass for contracts open at year end; no user data currently exercises it.
- **Workaround:** report §1256 contracts on Form 6781 from your broker's 1099-B (they're reported mark-to-market there); the export lists them but does not split or mark them. An index option whose root is not in the list is filed as an ordinary option — check it.

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

### A loss across two unjoined listings of one security is flagged, not denied
- **Where:** `src/taxjson/lib/xlist_loss_radar.py` (tax-logic CA-XLIST-05 / US-XLIST-04).
- **Current behavior:** two listings (a TSX line and its NYSE line) are one security only when ticker.map, a `.tt` JOURNAL line or a transfer journal joins them. A loss on one with the other listing of the same root bought within 30 days under an equal name is a run Warning, a `ticker-map --suggest` line (its `TOBASE` suggestion) and a `run --strict` stop — but the loss stays allowed until ticker.map says `TOBASE` (one security) or `DISTINCT` (two). Listings of different roots (a different-root dual listing) or names that differ are not flagged, nor is a `.tt`-only book (no names).
- **Why deferred:** a name match is evidence, not proof (two share classes, a CDR, another company reusing a root); joining on it would change the books silently.
- **Workaround:** answer each Warning with the `TOBASE` or `DISTINCT` line it names; `taxjson tips --online` clusters different-root listings by issuer name.

### Second-order superficial losses from the ACB bump's date
- **Where:** `src/taxjson/lib/core.py` (the deferral ADJUST is dated the trigger).
- **Current behavior:** with a rebuy, a partial sale inside the window and the rest sold later, the inner sale inherits part of the bump and can itself be denied and re-deferred; T4037 attributes the whole denied amount to the shares still held at day 30. Year totals agree unless the inner and outer sales straddle a year end; the extra DISALLOW row shows in `wash-sales`.

### Estimate classifies dividends by listing suffix when the books carry no ISIN
- **Where:** `taxjson estimate` / `lib/tax_estimate.py`; the issuer test in `taxjson_run.py` (`_issuer_is_canadian_by_symbol`).
- **Current behavior:** the estimate takes the issuer's country from its ISIN when the books carry one (IB rows); otherwise a `.TO` payer is treated as eligible-Canadian and a `.US` payer as foreign. The foreign tax credit uses the books' TAX rows (15% is assumed only when there are none). Without an ISIN (Questrade, RBC) a Canadian corporation held via its US line, or a US issuer on a `.TO` line, is misclassified; `taxjson tips` flags the cross-listing case (US-LISTING) only when the project also shows the `.TO` line (a holding, a dividend row or a ticker.map rule) or, with `--online`, when Yahoo knows a `.TO` twin — a Canadian issuer held only on its US line with no such sighting is not flagged. The s.126 credit is capped at 15% of the foreign dividends, not at the Canadian tax otherwise payable on them.

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
- **Current behavior:** every Canadian-source dividend gets the eligible gross-up (38%) and credit. Non-eligible dividends (small-business corporations, some REIT/LP distributions: 15% gross-up, smaller credit) are taxed HIGHER than that, so the estimate understates them. T5 box 18 capital-gains dividends (split-share and mutual-fund corporations) are no longer part of this: name them in taxjson.toml's `[[capital_gains_dividends]]` (README) and the estimate taxes them as capital gains (audit R1-62, S023-08). T3 trust allocations (interest, ROC, capital gains) are not split by type at all.
- **Why deferred:** brokers' activity exports do not carry the T5 box; the split is only known from the slip.
- **Workaround:** the estimate is disclosed as an estimate; use the T5/T3 slips for the return. (`taxjson reconcile-slips` reads only T5008 / 1099-B disposition slips; it does not check dividend slips.)

### reconcile-slips cannot read per-type-code T5008s or scope a slip to one broker
- **Where:** `src/taxjson/bin/taxjson_reconcile_slips.py`.
- **Current behavior:** comparison is per security. IBKR issues one T5008 row per type code (SHS / OPC / WTS / FUT) identified as "Various"; such a slip cannot be compared, so transcribe a per-security CSV. Several brokers' slips are reconciled together against the account's combined dispositions; a single broker's slip cannot be scoped to that broker's own sales when one account mixes brokers (the gains rows carry no broker).
- **Why deferred:** a per-type-code total mode needs each disposition's broker (to separate IB's sales from Webull's and RBC's in a mixed account) and a warrant/share split the books do not carry.

## Latent assumptions (audit-flagged, not firing on current data)

### Currency⇒exchange suffix map is duplicated in ~6 places
- **Where:** `base.py` (`BaseBrokerage.CURRENCY_EXT_MAP`), `corp_actions.py` (`_CURRENCY_SUFFIX`), `ib_extractor.py` (`_IB_CURRENCY_EXT`, plus the ISIN-country map `_ISIN_EXT`) and a partial copy in `webull.py` (`CURRENCY_EXT_MAP`) each hardcode `{'CAD':'TO','USD':'US','AUD':'AX','GBP':'L'}`; `price_chain.py` and `bin/taxjson_t1135.py` carry reverse/extended variants (suffix→currency, suffix→country).
- **Risk:** a non-G4-currency listing (EUR/CHF/JPY/…) or a USD security on a non-US exchange gets the wrong suffix, splitting/merging ACB pools; and the copies can drift when one is changed. The IB `IE→L` item above is one instance of this broader pattern. An explicit `.TO` in a Questrade or generic export is kept whatever the row currency (`SAMPLF.U.TO` bought in USD), so the known USD-on-TSX case no longer depends on the map. Fix: centralize the map in one helper. (Note: `ticker_map.map_ticker`'s blanket US→TO remap is only used in `generate_summary`, a diagnostic — **not** the live `apply_mapping` transaction path — so it does not silently merge real pools.)

### Sub-micro quantity tolerance in the US engine (crypto dust)
- **Where:** `lib/core.py` (US engine) — no row or lot below 1e-8 units is booked.
- **Current behavior:** a US row under 1e-8 units is left out (its money too), a lot residue of at most 1e-8 units is folded into the sale that closes the lot, and a sale's excess of at most 1e-8 units opens no position; each case is named in a warning (tax-logic US-CRYPTO-08). (The Canada pool walk keeps a coin residue of any size with its cost — only float noise, under 1e-11 of the position, drains; share pools keep the 1e-6 tolerance: tax-logic CA-CRYPTO-09.)
- **Why deferred:** the US lot epsilon also absorbs float noise in every FIFO lot split; a per-asset tolerance there needs its own fuzz audit. Effect: cents.

## Report semantics (by design — not a bug)

### `<account>.sum` vs `<account>_wash.sum` — pre-wash and post-wash reports
- **Where:** `src/taxjson/bin/taxjson_run.py:stage_account` writes `<account>.sum` and `stage_wash_pass` writes `<account>_wash.sum`.
- **Behavior:** For each taxable account, two summary files are emitted:
  - **`<account>.sum`** — gains computed WITHOUT cross-account `--sheltered` context. The engine's intra-account wash-sale logic (ITA s. 40(2)(g) for Canada; IRC §1091 for US) still fires on the account's own losses.
  - **`<account>_wash.sum`** — gains re-computed WITH the merged sheltered accounts passed as `--sheltered` context. Adds Rev. Rul. 2008-5 (US) / affiliated-balance (Canada) matching: a sheltered acquisition within ±30 days of a taxable loss disallows the loss.
- **Why this is intentional:** the pair is a deliberate debug check. Comparing the two files line-by-line surfaces which losses got disallowed only because of a cross-account match — useful for sanity-checking the data (and catching wrong-account-tagging errors before filing).
- **Which one do I file from?** **`<account>_wash.sum` is canonical** for the gains and the wash treatment: it includes the full cross-account wash treatment. `<account>.sum` is the pre-comparison baseline. Its TOTAL PROCEEDS / TOTAL COST lines are the engine's signed figures (short covers and written-option buy-backs count as negative proceeds), not Schedule 3 proceeds/ACB — take those from `taxjson form-export` (or the FOR THE RETURN block of `taxjson sum`).
- **Why not collapse them:** the pre/post comparison is the design's value-add. Future change candidate: bake the "POST-WASH (FILE FROM THIS)" / "PRE-WASH (DIAGNOSTIC)" label into a header line at the top of each file so the role is unambiguous when a user opens one in isolation.


### `taxjson audit` and the gains traces show a row's own id (intentional)
- **Where:** `src/taxjson/bin/taxjson_audit.py` (the EVENT header `#<id>`), `src/taxjson/lib/trace_format.py` (`id=` on the trace line, first 16 characters).
- **Behavior:** the id is printed unmasked so it can be pasted into `--id` (the `--summary` column is meant for that). For most brokers it is a content hash, but a Kraken row's id is the exchange's own ledger txid (`LG1GGG-...-fee`), so audit and the traces show it while the Kraken parser's own messages mask it as `LG***`.
- **Why this is intentional:** owner decision (audit A2-1379): a txid is an exchange reference, not an account number or a wallet address, and the id is the `--id` handle. Review audit and trace output before sharing it outside your records (SECURITY.md).
---

### T1135 cost amounts follow the books — custody transfer-ins carry only declared cost
- **Where:** `src/taxjson/bin/taxjson_t1135.py` (`TRANSFER` in `_NON_CAPITAL`; taxable books post-sidecar contain no TRANSFER rows at all).
- **Current behavior:** a position established by a custody transfer-in contributes to the T1135 cost-amount threshold only through whatever acquisition history the books carry (imported buys, `start_pos`/backdated `.tt` declarations, or the broker's stated book value booked for a transfer-in from outside the books). A transferred-in foreign position with lost history opened by an `OPENING ... cost=unknown` line shows as a missing-history opening with no cost (`taxjson t1135` applies the missing-history lines the way the gains stage does, and flags a still-held one "cost understated") — the threshold test can understate until the true history is declared.
- **Why this is the design:** T1135 cost amount IS adjusted cost base; the tool refuses to invent one from a transfer's arrival market value. Declare the real history (the same `custody_fixes.tt` pattern the wash engine prescribes) and the threshold is right.

### T1135 sees only the brokerage books
- **Where:** `src/taxjson/bin/taxjson_t1135.py` (`build_report`, `render_report`).
- **Current behavior:** the $100,000 test sums the cost of the foreign property in the taxable accounts' books. Specified foreign property held outside them — a foreign bank account or cash, shares held in certificate form, foreign real estate or a debt owed by a non-resident — counts toward the same threshold at the same time (ITA 233.3) and is not seen. The report says so beside its verdict ("on these books"), and the verdict is provisional ("so far") until the year has ended. A long option the books still hold after its expiry date is counted at cost and named.
- **Why deferred:** the books carry no such property, and its cost on each day of the year (the test is on the simultaneous total) needs an input of its own. Sketch: a `[t1135] other_property = [{cost, from, to, country}]` table added to the walk's daily total and the per-country table.

### Futures are booked on their settled P/L (both countries)
- **Where:** `src/taxjson/lib/futures.py` (applied by convert-currency / merge2 with the project's `--country`; it used to key on a CAD target), `core._trade_money` and the US engine's `tx_net`, `taxjson_form_export.build_schedule3`.
- **Current behavior:** a plain futures fill that opens a position carries no money, and a close carries the realized native P/L (average cost, commissions on both legs), converted at the closing leg's rate. So the per-account `.sum` shows COST 0 and PROCEEDS = the P/L for futures, `list`/holdings show an open futures position at ACB 0, and Schedule 3 line 6 shows a gain as proceeds and a loss as ACB. The P/L is realized at the close, not marked to market daily; converting each day's variation margin at that day's rate would differ by about P/L x the FX move over the holding period. Blended pools across accounts settle each account's futures separately. A futures row other than a BUYSELL fill (an OPENING_BALANCE, a transfer) stops the conversion with the row named. US books use the same settlement basis, with a partial close taken FIFO from the open contracts (Canada: average cost); §1256 marking and 60/40 are still not modelled (see above).
- **Why this is the design:** only the variation margin changes hands; the notional is never paid (ITA s.261(2)(b) converts amounts that arise), and the broker's T5008 reports futures the same way (cost 0, proceeds = P/L).

### Old per-purpose project files stop every command until migrated
- **Where:** `src/taxjson/lib/migrate.py` (`legacy_files`, `legacy_message`), called by every config reader (`taxjson_run._refuse_legacy_project_files`).
- **Behavior:** `yf_ticker.map`, `crypto_ticker.map`, `ticker_extraction_overrides.txt`, `t1135.map`, `amt_carryover.txt`, `claimed_losses.txt`, `capital_gains_dividends.map` and `distributions.map` are no longer read: their contents are `QUOTE` / `CRYPTO` / `EXTRACT` / `T1135` lines in `ticker.map` and `[estimate] amt_carryover`, `[carryover] claimed`, `[[capital_gains_dividends]]`, `[[distributions]]` in `taxjson.toml`. While one of the files is in a project, every command stops (exit 2) naming it and `taxjson migrate`; the stand-alone tools that used to look for a file in a folder (fill-crypto, harvest) refuse a folder that still holds it. The exception is `tv_exchange.map`, the removed TradingView export's file: nothing reads it, so no rule can be lost — it stops nothing (`taxjson run` notes it once) and `taxjson migrate` only renames it.
- **Why this is the design:** owner decision — no silent fallback: reading neither would drop the file's rules, reading both would leave two places to edit. `taxjson migrate` appends (never rewrites) and renames each old file `<name>.migrated` rather than deleting it; delete those once you have checked the result. The ticker.map files' comments are carried over; of the four data files only a `capital_gains_dividends.map` / `distributions.map` line's own trailing note is (as a comment above its table) — the renamed file keeps every comment.

## CLI silent-fail conditions

### `to_base_curr.py` real-time intra-day fetch
- **Where:** `src/taxjson/bin/to_base_curr.py` (`_spot_row`, the intra-day spot row; non-CAD base currencies only — a CAD base takes Bank of Canada daily rates and no spot row).
- **Current behavior:** if the intra-day spot fetch from yfinance fails, the failure is swallowed without a stderr message. The historical rates already emitted are unaffected.
- **Why deferred:** the most common failure mode is "market closed" (weekends, holidays, evenings). Logging on every off-market run would be steady noise drowning out real warnings.
- **Workaround:** the historical-rates path emits its own loud `WARNING:` line on a fetch failure, so a real outage is still visible — only the optional intra-day stamp is silent.

---

## Known engine corner cases (latent — not on the standard `taxjson run` path)

Corner cases the engine handles conservatively or only flags (the first is flagged on every `taxjson run`); documented so they are known.

### RESP accounts are treated as affiliated for the superficial-loss rule
- **Where:** every account with `type = "sheltered"` is an affiliated person in `lib/core.py`'s wash pass.
- **Question:** s.251.1(1)(g) affiliates a trust with its majority-interest beneficiary. CRA's T4037 treats an RRSP or TFSA as affiliated with its annuitant/holder, but an RESP subscriber is usually not a beneficiary, so whether an RESP purchase can deny the subscriber's loss is not settled.
- **Current behaviour:** conservative — an RESP purchase inside the window that is still held at its end denies the loss (permanently, as for any registered account). A filer who takes the other position has to adjust by hand.

### Foreign return of capital is only reclassified for IBKR (Canada projects)
- **Where:** in a Canada project, `lib/brokerages/ib_extractor.py` treats a "(Return of Capital)" distribution from a non-Canadian ISIN as a dividend (ITA s.90(1)) and a payment in lieu as income; a US project (and `taxjson-brokerage` without `--country canada`) keeps every return of capital as a basis reduction. Questrade and RBC exports carry no ISIN, and a `.US` listing does not prove a foreign issuer, so their ROC rows stay ACB reductions — check US-issuer ROC on those brokers by hand.

### A merger's per-account empirical ratios are blended
- **Where:** `lib/core.py` folds one merger's rename SPLITs with different per-account ratios into a single holdings-weighted ratio (2026-09). Totals and the shared ACB pool are right; each account's wash-walk balance can be a fraction of a share off.

### Payments in lieu: what the exports cannot say
- **Where:** `lib/income_dating.py` (`pil_is_dividend`), `lib/brokerages/ib_extractor.py`, `rbc_direct.py`, `questrade.py`.
- **Current behaviour:** in a Canada project a payment in lieu on a Canadian issuer's share paid by a Canadian dealer is a taxable (eligible) dividend (ITA s.260; tax-logic CA-INC-03); the dealer comes from the IB statement's BrokerName ("Interactive Brokers Canada Inc."). An IB file without that header row leaves the dealer unknown and the payment ordinary income. A payment in lieu on a Canadian TRUST unit is trust income under s.260(5.1)(b), not a dividend; the exports do not say which issuers are trusts, so it is counted as a dividend. RBC's "CASH IN LIEU OF DIVIDEND" and Questrade's "SUBST PAY ... IN LIEU OF DIVIDEND" rows are payments in lieu from a Canadian dealer (a Canadian issuer's is a dividend, a foreign issuer's ordinary income).
- **Workaround:** the dealer's T5 (box 24 and the other income boxes) is authoritative; compare with the TAXABLE line of `divs-sum` and with `dil-sum`.

### US January fund and REIT dividends need a list
- **Where:** `lib/income_dating.py`; tax-logic US-INC-DATE-RIC.
- **Current behaviour:** §852(b)(7) / §857(b)(9) put a fund or REIT dividend declared in October–December and paid in January on Dec 31, but no export says which payer is a fund. A US project keeps the pay date, warns when a January dividend has an October–December ex date (IB accruals, from any statement of the same IB account and matched to a posting within a week of the accrued pay date) or record date (Questrade/RBC), and moves the payments in `[settings] ric_january_dividends` to Dec 31.

## Conventions

- **Severity ranking:** items above are loosely ordered: gaps that drop tax-relevant data first, cosmetic / latent items last.
- **What counts as "fixed":** the item is removed from this file *and* a regression test pins the corrected behavior.
- **What counts as a "workaround":** any user-side action that makes the bug not fire on their data (manual edit, `ticker.map` override, splitting input files, etc.).
- **What does NOT belong here:** items that are simply unimplemented features (e.g. a brokerage we haven't written a parser for) — those go in `CONTRIBUTING.md` or roadmap docs.

---

## Graduated (fixed)

- **FX converter test coverage** — 2026-10: `to_base_curr.main()` and the Bank of Canada / noon-rate / Yahoo fetch-and-cache paths are covered with the fetchers mocked (`tests/test_fx_boc.py`, `tests/test_fix_a2_fx.py`).
- **Questrade interest** — Questrade `INT` rows are booked as INTEREST (sign kept); only the dividend withholding is still missing (entry above).
- **Option premium timing across a year end (ITA s.49(1); IT-479R paras 23–32)** — 2026-09: a written option's premium is now a gain in the year written under `option_premium_timing = "grant"` (Canada default), a buy-back a loss in its own year, an assignment folded with no grant record; `taxjson option-boundary` names any filed year to amend. Previously the premium was recognised at the close (the US §1234 convention).
- **Income dating and payments in lieu (partition Phase C, 2026-09-30)** — a Canadian trust's distribution is now income of its record-date year and its return of capital lowers the ACB on the record date (Questrade/RBC); a Canadian dealer's payment in lieu on a Canadian issuer's share is a dividend (s.260); US January fund/REIT dividends are warned about and can be listed. Previously every row was dated by its pay date and every payment in lieu was ordinary income.
- **Negative ACB after a return of capital (s.40(3))** — 2026-09: booked as a deemed gain in the distribution year with the ACB reset to nil; previously only a warning, with the whole amount landing in the sale year.
- **Re-short "superficial loss" (s.54)** — 2026-09: a new short sale or written option no longer triggers a denial of a cover loss (it acquires nothing); a long purchase held at day 30 still does. Previously the US §1091(e) re-short branch applied.

### Broker-parser coverage gaps (graduated 2026-09-14)
Kraken stablecoin (`USDC`/`USDT`/`DAI`) `earn/reward` rows were folded
to the symbol `USD` before pricing, which `taxjson-fill-crypto` refuses
to price — the income booked at $0; the reward now keeps its coin
name and is priced at 1.0/unit (net = qty) with no fake acquisition
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
recognized non-events of the position books (see the IB Forex and
Kraken fiat items above) instead of fake `USD`/`CAD` trades; Questrade `FCH`
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
property) even though trade books fold the USD stablecoins to USD for
pricing. Kraken Earn allocation/deallocation shuffles (paired rows)
remain ignored — internal moves; a `hybridearnwithdrawal` row has no
counter-leg and is custody evidence like a withdrawal (a TRANSFER in the
sidecar). A Kraken coin fee on a withdrawal/deposit is named in the
TRANSFER's description (`withdrawal (fee 0.05 ABC)`) and booked as its
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
fee, a sale at fair value (2026-10, audit R1-26). The pairing is a
minimum-loss assignment (also split deposits / merged sends), a Kraken
Hybrid Earn move is never paired, a saved gift/payment the pairing
overrides is warned about (`--unpair` keeps it), and a full `run` parses
every crypto account before pairing (re-audit 2). Limits: pairing reads the crypto accounts'
sidecars only (a send to an equity or `transfers = true` account looks
unmatched); a US move between two crypto accounts carries the moved
lots to the receiving account (blended crypto pass, 2026-10, re-audit
A2-0003); the stablecoin pool is rebuilt from Kraken ledgers and
Coinbase exports (a Kraken trades export without its ledger is not
read for it) and does not add a superficial loss back into the pool's
cost.

- **2026-09 (crypto blended pass):** a Canadian project with two or more taxable `crypto = true` accounts now runs ONE blended crypto pass as well (s.47 averaging and the superficial-loss rule across exchanges); each exchange's book was computed alone before, while the run's overlap note claimed the blend covered them. `audit` and `check-filed` recompute the same way; US crypto (no §1091) stays per account. Tests: `tests/test_crypto_blend.py`.
- **2026-08 (blended taxable pass):** the multi-account gap is fixed. One combined gains run now produces the canonical wash-adjusted artifacts for all taxable equity accounts: Canada ACB blends across non-registered accounts (ITA s.47) and US §1091 matches cross-account while FIFO basis stays per account (`taxjson-gains --per-account-basis`); `taxjson-split-gains` rebuilds the per-account files, so every consumer reads its usual filenames. The per-account `<name>.sum` remains the isolated pre-blend baseline (deliberate diagnostic pair). Tests: `tests/test_blended_taxable.py`.
- **2026-08:** `taxjson run --strict` — per-account validation ERRORs promoted to fatal (the non-fatal default remains); `crypto_ticker.map` — user-editable Yahoo-collision overrides for `taxjson-fill-crypto` (since folded into ticker.map `CRYPTO` lines, the only coin ids; the built-in table was removed); Coinbase "Convert" rows — two-leg SELL+BUY emission for the recognized `Converted X AAA to Y BBB` Notes pattern (unknown variants still fail hard); Kraken trades-CSV crypto/crypto pairs — two USD-denominated legs mirroring the ledgers path; Kraken legacy concatenated pair formats (`XXBTZUSD`, `XETHXXBT`, `ADAUSD`) — recognized explicitly, unknown shapes refuse loudly; Kraken `_normalize_asset` — X-prefix strip extended to the full enumerated set (XLM/XMR/ZEC/XDG→DOGE/ETC/MLN/REP); Webull blank-`Description` carry-over — reset when the symbol changes; crypto-vs-equity path mismatch — `taxjson run` now refuses a crypto-broker file in an equity account (and vice versa); interactive corp-actions stage — stale `.diag` sidecars are cleared. Regression tests: `tests/test_audit_2026_08_fixes.py::TestKnownIssuesGraduated`.
