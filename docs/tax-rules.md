# Tax rules

Every tax rule taxjson implements, one entry per rule, Canada and the United States in separate parts. Canadian and US law never mix in taxjson: a project has one country (`[settings] country`), and each rule below applies only in its own country's part.

How to read an entry:

- **Rule:** what taxjson does, in short. The authoritative wording is `taxjson tax-logic --ids` in a project (it fills in the project's own settings); this file is the map from that text to the law and the code.
- **Source:** the citation as `REFERENCES.md` gives it. Where `REFERENCES.md` has no row, the entry says so and names the tax-logic rule that states the behaviour (and the section that rule's own text cites, if any).
- **Rule ids:** the ids in `src/taxjson/lib/tax_logic.py`. Tests that pin a rule carry the id (`@rule("CA-SL-02")`); `scripts/check_tax_rules.py` fails on an unknown id or an untested rule. Ids marked *(setting)* are stated only when a non-default setting is in force.
- **Code:** where it is implemented (`path` — `symbols`). The two engines are `CanadaTaxRules` and `USATaxRules` in `src/taxjson/lib/core.py`; their `compute_gains` methods are thousands of lines long, so search them for the helper named.
- **Edge cases and limits:** what the rule does not cover, from `KNOWN_ISSUES.md` and the code.

Settings named here are explained in `docs/settings.md`. taxjson computes; it does not give tax advice.

---

# Part 1 — Canada

## Tax year: the settlement date

- **Rule:** a trade belongs to the year it settles (`tax_date = "settle"`, the default): a sale on Dec 31 that settles in January is next year's disposition. Settle dates come from the broker when printed (one earlier than the trade date is refused, one more than 7 days after it is flagged); otherwise the standard cycle of the listing's market (T+1 in North America from May 2024, T+2 from 2017-09-05, T+3 before; other markets on their own dates), skipping weekends and settlement holidays. Options settle T+1; an exercise or assignment takes its stock leg's date; an expiry is dated its expiry day. Futures settle on the trade date. Crypto settles on the trade date. A US overnight-session, CME evening or Cboe Global Trading Hours fill is dated the next trading day. Rows at one moment keep the export's order. Interest, other income and a corporation's dividends belong to the year they are paid (trusts: see "Income dating").
- **Source:** CRA technical interpretation **2012-0468931C6** (a publicly traded share is disposed of on the settlement date).
- **Rule ids:** `CA-DATE-01`, `CA-DATE-02` *(setting: `tax_date = "trade"`)*, `CA-DATE-03` … `CA-DATE-09`, `CA-DATE-10` *(setting: `futures_settle = "next_day"`)*, `CA-DATE-11`, `CA-DATE-13` … `CA-DATE-18`, `CA-DATE-SESSION`.
- **Code:** `src/taxjson/lib/dates.py` — `settlement_date`, `market_of`, `settlement_lag_days`; `src/taxjson/lib/market_calendar.py`; `src/taxjson/lib/country.py` — `resolve_tax_date`, `futures_settle_mode`; `src/taxjson/lib/corporate_timeline.py` — `event_sort_key`; `src/taxjson/lib/brokerages/ib_extractor.py` — `_ib_split_datetime`.
- **Edge cases and limits:** outside the US and Canada only weekends are skipped (a local bank holiday is not), so such a settle date can be a day early; venues other than the listed Asian exchanges keep IB's Eastern clock date (KNOWN_ISSUES "Settlement calendars outside North America"). Webull prints the settle date and the trade date is walked back one cycle (`CA-DATE-17`). Rows of one account at one moment from two files follow the files' name order (`CA-DATE-18`). Schedule 3's "year of acquisition" is counted on trade dates (`CA-DISP-07`).

## Foreign currency: Bank of Canada rates

- **Rule:** every amount is converted to CAD at the Bank of Canada rate of its date (the settle date for a trade): the daily average from 2017-03-01, the noon rate before (from 2007-05-01), Yahoo only before May 2007 or for a currency the Bank does not publish. No annual average. A rate up to 12 days old is used across gaps; a row with no rate after that stops the run naming the date and currency pair. taxjson carries no built-in rate.
- **Source:** Folio **S5-F4-C1** *Income Tax Reporting Currency*; T4037 "Foreign currencies".
- **Rule ids:** `CA-FX-01`, `CA-FX-02`, `CA-FX-03`, `CA-CTRY-03` (base currency must be CAD).
- **Code:** `src/taxjson/bin/to_base_curr.py` — `fetch_boc`, `fetch_boc_noon`, `fetch_yahoo`, `build_rates`, `MAX_FILL_DAYS`; `src/taxjson/bin/taxjson_run.py` — `stage_currency_rates`.
- **Edge cases and limits:** the stand-alone converters accept your own rate (`--default-rate`); `taxjson fx-cash` and `taxjson crypto-sends` leave an unrated event unrated rather than stop. A failed intra-day spot fetch (a non-CAD base only) is silent (KNOWN_ISSUES "to_base_curr.py real-time intra-day fetch").

## Capital gain and the Schedule 3 lines

- **Rule:** gain = proceeds − ACB − outlays, reported in full (half of the net gain is taxable; you apply the inclusion on Schedule 3). Shares and fund units go on line 13199/13200, options and futures on 15199/15300, accounts marked crypto on 15200/15301 from 2025 (15199/15300 before). The 2024 form is split at June 24, 2024 into Period 1 and Period 2 codes. A short sale's gain is realized when covered; a cash-settled option realizes on the option itself. A written option's premium (grant timing) is shown gross as proceeds with its commission as an outlay. Cells are rounded half-up to the cent and ACB is the footing residual; a net commission rebate stays in the proceeds, so outlays are never negative.
- **Source:** s.40(1); Guide **T4037** *Capital Gains*, "Calculating your capital gain or loss"; inclusion rate s.38(a), Schedule 3 line 19900; Schedule 3 and Guide **T4091** *T5008 Guide* (the taxpayer's ACB governs, not the slip).
- **Rule ids:** `CA-DISP-01` … `CA-DISP-08`.
- **Code:** `src/taxjson/bin/taxjson_form_export.py` — `build_schedule3`, `schedule3_line`, `schedule3_period`, `_foot_cells`; `src/taxjson/lib/core.py` — `CanadaTaxRules`; `src/taxjson/bin/taxjson_reconcile_slips.py`.
- **Edge cases and limits:** `<account>.sum` TOTAL PROCEEDS / TOTAL COST are the engine's signed figures (short covers and buy-backs count as negative proceeds), not Schedule 3 cells: take those from `taxjson form-export` or the FOR THE RETURN block of `taxjson sum`; `<account>_wash.sum` is the canonical gains file (KNOWN_ISSUES "`<account>.sum` vs `<account>_wash.sum`"). `taxjson reconcile-slips` compares per security and cannot read IBKR's per-type-code T5008 rows.

## ACB: average cost, one pool across your taxable accounts (s.47)

- **Rule:** one average-cost pool per identical property across all your taxable accounts. Purchase commissions add to the ACB; sale commissions are outlays. A commission refunded later (an IB Commission Adjustments row naming the trade, even in another statement) nets against that trade's commission. Registered accounts (`type = "sheltered"`) are tracked but kept out of the filing totals. Two or more taxable crypto accounts are pooled the same way, in one blended crypto pass.
- **Source:** s.47(1); **IT-387R2** *Meaning of Identical Properties*; T4037 "Identical properties".
- **Rule ids:** `CA-ACB-01`, `CA-ACB-02`, `CA-ACB-03`, `CA-ACB-05`, `CA-ACB-COMMREFUND`.
- **Code:** `src/taxjson/lib/core.py` — `CanadaTaxRules`; `src/taxjson/bin/taxjson_run.py` — `stage_blended_wash_pass`; `src/taxjson/lib/country.py` — `basis_pooled_across_accounts`; `src/taxjson/lib/brokerages/ib_extractor.py` — `_ib_fold_refund`.
- **Edge cases and limits:** the single pool needs a full `taxjson run` (not `--account`, and no elections pending) (`CA-ACB-03`). `<account>.sum` is the per-account pre-blend baseline. A merger's per-account empirical ratios are blended, so one account's walk can be a fraction of a share off (KNOWN_ISSUES). A refund that names no single trade stays a separate fee.

## Identical property: listings, cross-listings, broker codes and renames

- **Rule:** identical property is the same symbol with its listing suffix. A Canadian listing is one symbol whatever venue the input names (`ROOT.TO`; `.V`, `.VN`, `.CN`, `.NE` fold into `.TO`). Two other listings are one security only when ticker.map joins them (`TOBASE`, `GLOBAL`; a legacy `JOURNAL` line is read as `TOBASE`), when a `.tt` line of an account declares a journal between them (`JOURNAL <date> FROM TO <qty>`: no disposition, in both countries, when the two symbols share one root — `QZG.TO`, `QZG.U.TO`, `QZG.US` — or names that agree as a broker journal's legs' must, and no names of two companies; otherwise the run stops and a `TOBASE` line is the deliberate join; the legs kept with the account's transfer evidence, moving units inside that account only; a duplicate of the broker's own journal legs is booked once; never an option or a future), or when `taxjson run` joins them itself: a transfer journal pairs them uniquely (same quantity, within 5 business days) and the exports' security names are equal word for word once normalised. A broker's explicit journal between the two listings (one account, one day, both legs in its journal wording: RBC's TFR `TRANSFER TO C$` / `FROM U$`, IB's InterDepot, Questrade's BRW `JOURNAL POSITION`) compares its two legs' own names instead, a corporate-form word ending one name only (never one inside a name, never `LP`), a leading `THE` or a `COM NEW` spelling set aside; such legs pair on their day first, a reference both legs carry (RBC `J~…`, Questrade's journal pair) is their pair id, and a listing several journals map onto is no ambiguity when those listings are one security with each other too (each equal word for word to the shared listing's name of its day, or their own names agreeing; a PLC and a CORP that each match a name stating no form stay suggestions). Each automatic join is a Warning naming the `DISTINCT X Y` line that undoes it. A Questrade or RBC bare ticker takes its listing from the row currency unless the books name the other listing: a transfer-in that is the unique arrival (same quantity, within 5 business days, an equal name) of another broker's transfer out of another ticker on the same currency's listing, or of the same ticker's other listing, or shares that arrived by an unpaired transfer whose `.TO` listing is in the books under an equal name, are booked as that listing (a Warning with the `DISTINCT ROOT.US ROOT.TO` undo). A broker's currency journal between a security's CAD and USD lines (Questrade's BRW pair `... JOURNAL POSITION TO USD` / `... JOURNAL POSITION FROM CAD BOOK VALUE: $X CNV@ r`, or the reverse, in one account on one day) is not a disposition: the two lines are joined as a `TOBASE` line would (the USD line is the account's own USD listing of the security, else `SYMBOL.U.TO` by the TSX convention in `data/markets.toml`); the units keep the pool's ACB. A broker's internal security code (Questrade's letter + digits on transferred-in rows) is resolved to the line a currency-journal leg of the same name in the account names, the security the account's own trades, the arriving transfer, or one exactly matching name identify; a code nothing identifies stays its own security (ATTENTION with the `GLOBAL` line to add). A ticker change is a dated event (a broker's corporate-action row, a `.tt` line `RENAME <date> OLD NEW` in any account — applied to every account holding OLD, recorded once — or IB's one contract id under two symbols, booked with a Warning): on that date the pool, its ACB and acquisition dates carry from OLD to NEW, and the superficial-loss rule treats OLD before and NEW after as identical.
- **Source:** s.47(1); **IT-387R2**; T4037 "Identical properties" (cross-listings of the same share are identical, CDRs are not).
- **Rule ids:** `CA-ACB-04`, `CA-XLIST-01`, `CA-XLIST-02`, `CA-XLIST-03`, `CA-XLIST-04`, `CA-ACB-CODES`, `CA-ACB-RENAME`, `CA-CORP-02`.
- **Code:** `src/taxjson/lib/cross_listings.py` — `analyze`, `_names_verdict`, `_journal_names_verdict`, `journal_wording`, `business_days`, `map_lines`, `companies_differ`, `collisions`, `extract_words`; `src/taxjson/lib/brokerages/questrade.py` — `_plan_qt_journals`, `_journal_listing`, `journal_codes`; `src/taxjson/lib/markets.py` — `usd_unit_listing`; `src/taxjson/lib/brokerages/ib_extractor.py` — `ib_temp_symbol_ticker`, `_ib_temp_folds`, `_warn_stock_aliases`; `src/taxjson/lib/symbol_codes.py` — `resolve`, `names_agree`, `exact_name`; `src/taxjson/lib/renames.py` — `apply_dated_renames`, `late_rows`, `unresolved_late`; `src/taxjson/lib/dated_events.py` — `read_declarations`, `settle_journals`, `journal_legs`; `src/taxjson/bin/taxjson_ticker_map.py` — `merge_renames`; `src/taxjson/lib/listing_suffix.py` — `resolve`, `scan_questrade`, `scan_rbc`; `src/taxjson/bin/taxjson_run.py` — `stage_cross_listings`, `stage_symbol_codes`, `stage_listing_suffix`, `cmd_renames`; `src/taxjson/lib/brokerages/base.py` — `canonical_ca_listing`.
- **Edge cases and limits:** a trade in OLD after a dated rename is a different security until its declaration says `late=fold` or `late=separate` (a `.tt` RENAME line); such trades stop `run --strict` and are listed by `taxjson renames`. A ticker.map rule that writes `ROOT.V` still splits the pool from `ROOT.TO`. A CSE/NEO ticker that duplicates a different TSX ticker shares its pool (KNOWN_ISSUES "Canadian listings carry no venue"). Anything less certain than an exact-name journal pair is only a suggestion (`taxjson ticker-map --suggest`), and two listings whose names name different companies are not even suggested. A `.US` symbol whose rows name two different companies, one a Canadian-listed fund's US-dollar units, is a symbol collision: a Warning and an `EXTRACT ... | USD | ROOT.U.TO` suggestion (plus its `TOBASE`), never a join. IB's temporary time-stamped symbol (YYYYMMDDHHMMSS then the ticker) under the ticker's own contract id is the ticker, unless a ticker.map line names the stamped symbol (the line decides); a ticker change IB shows only as one contract id under two symbols is booked as a dated rename (a ticker.map `DISTINCT OLD NEW` line undoes it); a weaker look-alike (Questrade, RBC, Webull) is only suggested, as the `.tt` line. Identification never changes a tax rule.

## Superficial loss (s.54)

- **Rule:** a loss is denied when identical property is acquired within 30 days before or after the sale (settle dates) and still held at the end of day 30, in your taxable or registered accounts. For each sale on its own, denied units = the least of the units sold, the units acquired in the window and the units held at day 30, the last two taken per holder (your taxable accounts as one pool, each registered or affiliated account alone) and summed; the same held unit may back two sales' denials. The denied amount is added to the replacement's ACB and comes back when it is sold. Replacements are matched after-the-sale first, then earlier ones latest first. A long call on the shares is identical property at its contract size; a put never replaces shares; shares never replace an option. A warrant or right, an adjusted option series (root + digit) or a futures option bought in the window is only flagged for a manual check. Only purchases count: a new short sale or written option never replaces. A loss on buying back a written option is exempt by default. Crypto follows the same rule, pooled across exchanges. The planning tools (`taxjson wash-radar`, `taxjson sell-check`, `taxjson buy-check`, `taxjson harvest`, `taxjson watch`) apply the same rule on settle dates.
- **Source:** s.54 "superficial loss"; s.40(2)(g)(i); s.53(1)(f); T4037 "Superficial loss"; **s.251.1(1)(g)** (affiliated persons).
- **Rule ids:** `CA-SL-01` … `CA-SL-11`, `CA-SL-12` *(setting: `option_buyback_loss_superficial = true`)*, `CA-SL-13`, `CA-SL-14`, `CA-SL-15`, `CA-PLAN-01`, `CA-PLAN-02`, `CA-PLAN-04`, `CA-RPT-07`.
- **Code:** `src/taxjson/lib/core.py` — `CanadaTaxRules`, `_holder_rank`, `disposition_groups`, `detect_option_replacement_matches`, `detect_right_replacement_matches`, `option_contract_size`; `src/taxjson/bin/taxjson_wash_radar.py` — `main`; `src/taxjson/bin/taxjson_harvest.py`; `src/taxjson/lib/edge_cases.py`; `src/taxjson/lib/checklist.py` — `d_wash_reviewed`.
- **Edge cases and limits:** a spouse's or controlled corporation's purchases count only when given: `taxjson run` never reads them, so declare their account `type = "sheltered"` (KNOWN_ISSUES "Purchases by an affiliated person"). When both a taxable and a registered account bought in the window, which absorbs the denial follows the matching order, a stated policy (KNOWN_ISSUES "Superficial-loss attribution"). A partial sale inside the window can inherit part of the bump and be denied again (KNOWN_ISSUES "Second-order superficial losses"). A contract size is ASSUMED 100 when a non-IB export does not state it (ticker.map `MULT` overrides). Flags deny nothing: `taxjson wash-sales` lists them and the checklist's wash-reviewed step stays open. The planning tools see only the project's accounts.

## Registered and affiliated holders: permanent denial

- **Rule:** when the replacement sits in a registered plan (RRSP, TFSA, FHSA, LIRA, RESP ...) the ACB bump is worthless, so that part of the loss is reported as permanently denied, not deferred. A replacement bought by an affiliated person is denied on your return too, and that person adds it to their own ACB. A registered account's shares held before the window never create or back a denial.
- **Source:** s.53(1)(f) applied to an exempt trust; CRA's position on RRSP/TFSA repurchases (T4037 example); **s.251.1(1)(g)** (a trust is affiliated with its majority-interest beneficiary).
- **Rule ids:** `CA-SL-03`, `CA-SL-04`, `CA-SL-09`, `CA-ACB-05`.
- **Code:** `src/taxjson/lib/core.py` — `CanadaTaxRules`, `permanently_disallowed`, `_holder_rank`.
- **Edge cases and limits:** an RESP is treated as affiliated (the conservative choice; whether an RESP subscriber is affiliated is not settled — KNOWN_ISSUES "RESP accounts are treated as affiliated").

## Registered-account transfers

- **Rule:** a registered account's transfer in or out is a move between accounts, not an acquisition or disposition: its shares count as held at day 30, but it never replaces a loss. One warning per run lists each transfer-in inside a taxable loss's window and each netted move with a leg inside it (`transfers_as_acquisitions = false`, the default). With `true` every such transfer is booked as an acquisition or disposition on its date, and a transfer-in inside a loss's window stops the run until declared. A transfer that is one leg of an in-kind contribution or withdrawal (a taxable account on the other side) is not a move between accounts: see the next entry.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rules `CA-SL-16` and `CA-SL-17`.
- **Rule ids:** `CA-SL-16`, `CA-SL-17` *(setting: `transfers_as_acquisitions = true`)*.
- **Code:** `src/taxjson/lib/pipeline.py` — `_handle_transfers`, `_drop_self_cancelling_transfers`, `transfers_in_loss_windows`, `transfers_as_acquisitions`; `src/taxjson/bin/taxjson_run.py` — `_say_transfer_windows`.
- **Edge cases and limits:** a transfer from outside your books (another person, a broker not in the project) stays a move; record a genuine purchase as a `.tt` BUYSELL.

## In-kind contributions and withdrawals (taxable ↔ registered)

- **Rule:** a taxable account's transfer-out paired with a registered account's transfer-in of the same security and quantity within 10 days (or the reverse; across brokers) is an in-kind move. Pairing order: legs of one account first (a journal), then legs of the same kind — taxable with taxable, registered with registered, the closest date first, a move of your own — across the whole project, and only then a taxable leg with a registered one; that pair is booked only when neither leg has another plausible partner (a leg of either kind, same security and quantity, in the window), else it is listed NOT booked as ambiguous with both candidates and the `.tt` line that settles it (`run --strict` stops). An `INKIND` line declares the move of its transfer row (`plan=` picks the plan's leg); `INKIND <date> <symbol> <qty> plan=own` declares the row a move of your own. A contribution is booked in the taxable account as a sale at fair market value on the transfer date: a gain is taxed, a loss is nil for good — not a superficial loss, never added to an ACB, shown on its own line by `sum` and form-export ("Denied: contribution to a registered plan", `denied_contribution`), not in DENIED (an RESP or PRPP is not named by s.40(2)(g)(iv): its loss is an ordinary one; an account whose plan is not named is taken as one that is). The plan's acquisition is a purchase for s.54 on its own date whatever `transfers_as_acquisitions` says, so a taxable loss on the same security within 30 days, the plan holding at day 30, is lost for good. A withdrawal is a purchase at fair market value (its ACB); its value is RRSP/RRIF income on the T4RSP/T4RIF (noted, not booked) and is not taxed from a TFSA. The value: a `.tt` `INKIND` line, else the market value the broker states on the transfer row (IB), else Yahoo's close on the date (ESTIMATED), converted at the Bank of Canada rate of the date; with `TAXJSON_OFFLINE` and no cached close the run stops and names the line to add. One warning per run lists every move.
- **Source:** s.40(2)(g)(iv) (a loss on a disposition to an RRSP, RRIF, TFSA, FHSA or RDSP trust is nil); s.54 "superficial loss" with s.40(2)(g)(i) for the plan's acquisition; CRA guides T4040 and RC4466 (a contribution in kind is a disposition at fair market value). `REFERENCES.md` has no row for the pairing and the value order: see `taxjson tax-logic` rules `CA-INKIND-01` and `CA-INKIND-06`.
- **Rule ids:** `CA-INKIND-01` … `CA-INKIND-06`.
- **Code:** `src/taxjson/lib/in_kind.py` — `pair_all`, `line_legs`, `in_parts`, `apply_lines`, `value`, `booked_rows`, `mark_sheltered`, `message`, `parts_message`; `src/taxjson/bin/taxjson_run.py` — `in_kind_state`, `stage_in_kind_context`, `_say_in_kind`, `stage_transfer_arrivals`; `src/taxjson/lib/core.py` — `CanadaTaxRules`, `IN_KIND_CONTRIBUTION_TYPE`; `src/taxjson/lib/price_chain.py` — `close_on`; `src/taxjson/bin/taxjson_convert_tt.py` — `parse_inkind_line`; `src/taxjson/bin/taxjson_form_export.py` — `build_schedule3`.
- **Edge cases and limits:** only equal quantities pair on their own: a transfer-out a plan received in parts (or the reverse) is warned about ("possibly in-kind in parts"), not booked; an `INKIND` line for the taxable row books it with the plan's legs that add up to it (when exactly one set does), `plan=own` silences it. Two identical moves on one day are two sales. Yahoo's close is split-adjusted: a split after the date makes it wrong (use an `INKIND` line). A move whose value cannot be found is listed NOT booked (`run --strict` stops) and the taxable books keep the shares. The income of a withdrawal is not booked.

## Options: premium timing (s.49)

- **Rule:** writing an option is a disposition: the premium is a capital gain in the year written (`option_premium_timing = "grant"`, the default, for contracts written from `option_grant_timing_since`; earlier contracts keep close timing). Buying it back is a capital loss in the buy-back year; expiry adds nothing. A bought option's cost is a loss on its expiry date. Under `"close"` nothing is taxed until the position closes, and the premium minus the cost is the gain or loss on that date. When a premium's year was already filed and the option is later exercised or assigned, `taxjson option-boundary` names the grant year to amend.
- **Source:** s.49(1)–(4); **IT-479R** *Transactions in Securities*, paras 23–32 (para 29 for calls, para 32 for puts).
- **Rule ids:** `CA-OPT-01`, `CA-OPT-02`, `CA-OPT-03`, `CA-OPT-04`, `CA-OPT-05` and `CA-OPT-10` *(setting: `option_premium_timing = "close"`)*, `CA-OPT-07`, `CA-SL-11`.
- **Code:** `src/taxjson/lib/core.py` — `CanadaTaxRules`, `_grant_applies`, `_open_short_option`, `_recognised_premium`; `src/taxjson/lib/pipeline.py` — `option_timing_from_settings`; `src/taxjson/lib/option_boundary.py` — `write_lots`, `straddling`, `filed_locks`.
- **Edge cases and limits:** set `option_grant_timing_since` once to the first year filed under grant timing and keep it unchanged in later projects; a written option carried out of a closed year under another timing is flagged by `taxjson handoff` (taxed twice, or never) (`CA-RPT-08`). Whether naked option writing is on income account is the user's question (IT-479R para 25(c); `REFERENCES.md` "Capital vs income character").

## Option exercise and assignment; warrants and rights

- **Rule:** on exercise or assignment the premium folds into the shares' cost or proceeds (a written put's premium reduces the cost of the shares acquired; a written call's is added to the proceeds; a holder's exercised put reduces the proceeds), and the grant year is amended. Each assignment's premium goes to its own stock leg: same account and underlying, the delivered quantity (contracts × contract size), priced at the strike, dated 3 days before to 7 days after the option row; several at one moment are told apart by strike. Exercising a warrant or right is not a disposition: its cost and the exercise price become the shares' ACB.
- **Source:** s.49(1)–(4); **IT-479R** paras 23–32. The warrant rule (`CA-OPT-09`) is stated in `taxjson tax-logic`, which cites s.49(3).
- **Rule ids:** `CA-OPT-06`, `CA-OPT-08`, `CA-OPT-09`.
- **Code:** `src/taxjson/lib/core.py` — `_pair_assign_legs`, `_AssignPremiumLedger`, `exercise_target`, `is_assign_premium_leg`; `src/taxjson/lib/brokerages/webull.py` — `_mark_assignments`.
- **Edge cases and limits:** Webull exports have no exercise code: a pair is inferred only with `[accounts.NAME] exercise_fee` set and the charge on the stock leg (KNOWN_ISSUES "Webull exercise/assignment inference"). The generic importer has no assignment target: book the pair as `.tt` ASSIGN rows. An IB warrant leg that cannot be paired is a disposal at 0 with an ATTENTION line; RBC refuses the file.

## Futures

- **Rule:** a futures contract is booked on its settled P/L: nothing is paid to open one, so its notional is never converted. Each close's P/L (commissions included, average cost of the open contracts) is converted at the closing leg's rate; Schedule 3 shows a gain as proceeds and a loss as ACB. Options on futures are ordinary options. Futures settle on the trade date unless `futures_settle = "next_day"`.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rules `CA-FX-04` … `CA-FX-06` (KNOWN_ISSUES "Futures are booked on their settled P/L" cites s.261(2)(b)).
- **Rule ids:** `CA-FX-04`, `CA-FX-05`, `CA-FX-06`, `CA-DATE-09`.
- **Code:** `src/taxjson/lib/futures.py` — `settle_futures`, `method_for`; `src/taxjson/bin/taxjson_form_export.py` — `build_schedule3`.
- **Edge cases and limits:** the P/L is realized at the close, not marked to market daily, so the CAD figure can differ by about P/L × the FX move over the holding period. A futures row other than a BUYSELL fill stops the conversion. A generic-import futures row at a negative price is read as its magnitude (KNOWN_ISSUES "A negative futures price").

## Splits, consolidations and name changes

- **Rule:** a split or consolidation scales the quantity; the ACB is unchanged. A trade executed before a split but settling after it is re-denominated. A name change carries the pool automatically.
- **Source:** s.47; T4037 "Stock splits".
- **Rule ids:** `CA-CORP-01`, `CA-CORP-02`.
- **Code:** `src/taxjson/lib/corporate_timeline.py` — `SplitTimeline`, `lineage_factor`, `cumulative_factor`; `src/taxjson/lib/corp_actions.py` — `NAME_CHANGE`, `_emit_rename`; `src/taxjson/lib/core.py` — `_redenoms`, `SplitStraddlesSettlementError`.
- **Edge cases and limits:** a rename-split dated between a trade's execution and its settlement is refused (`SplitStraddlesSettlementError`): re-date the row's settlement or the split. Cash in lieu of a fractional share is a sale of the fraction (`CA-CORP-05`); IB's cash-in-lieu wording is modelled, not seen in a real statement (KNOWN_ISSUES "IB cash-in-lieu row wording is unverified").

## Mergers and takeovers

- **Rule:** a share-for-share merger needs an election in the account's `manifest.json`: `taxable_disposition` (old shares sold at FMV, new shares cost FMV) or `rollover_s_85_1_5` (cost carries over). Cash in lieu of a fraction is a sale of the fraction on the pool's average cost. A merger paid wholly in cash is a sale at the cash proceeds. A merger paying shares AND cash is not modelled: the run stops on it as UNSUPPORTED. `ignore` skips broker noise only.
- **Source:** s.85.1, s.86, s.87; T4037 "Shares".
- **Rule ids:** `CA-CORP-03`, `CA-CORP-04`, `CA-CORP-05`, `CA-CORP-08`, `CA-CORP-09`, `CA-CORP-10`.
- **Code:** `src/taxjson/lib/corp_actions.py` — `CANADA_MERGER`, `_canada_merger_taxable`, `_canada_merger_rollover`, `_ib_unsupported_events`, `Manifest`; `src/taxjson/bin/taxjson_run.py` — `cmd_elect`.
- **Edge cases and limits:** enter a stock-plus-cash merger by hand in a `.tt` file and elect the event `ignore` (KNOWN_ISSUES "IB stock-plus-cash mergers are not booked"). A merger the broker booked at $0 needs an `fmv_per_share` hint for a taxable disposition.

## Spin-offs: s.86.1 rollover or taxable deemed dividend

- **Rule:** a spin-off is elected per event: `rollover_s_86_1` splits the parent's ACB between the two by the CAD amount you enter (`allocated_acb_cad`: the parent's ACB in CAD × the spin-off's share of the combined FMV right after it), booked exactly even on a foreign listing, and you file the s.86.1 election; or `taxable_deemed_dividend`, a dividend at FMV that is also the new shares' cost.
- **Source:** s.86.1; T4037 "Eligible distributions".
- **Rule ids:** `CA-CORP-06`, `CA-CORP-07`.
- **Code:** `src/taxjson/lib/corp_actions.py` — `CANADA_SPINOFF`, `_canada_spinoff_rollover_s_86_1`, `_canada_spinoff_deemed_dividend`, `HINTS_BY_ELECTION`, `FILING_REQUIRED_ELECTIONS`, `zero_basis_rollover_rows`.
- **Edge cases and limits:** a rollover booked with $0 allocated cost keeps the parent's whole cost and moves the gain to the spin-off's sale; every run warns about it. A company's Form 8937 percentage is a US figure and can differ from the s.86.1(3) split. An older manifest's `allocated_acb` (in the event's currency) is converted at the spin-off date's rate, with a warning to re-elect with `allocated_acb_cad`. A Canadian parent's tax-deferred spin-off (a butterfly) has no election of its own: book it with `rollover_s_86_1` and the allocated ACB (KNOWN_ISSUES "Spin-off default wording").

## Cost of property received as income or a dividend in kind

- **Rule:** property received as income takes that income as its cost: the shares of a spin-off elected `taxable_deemed_dividend` cost their FMV; a staking reward's fair value when received is the coins' cost; a stock dividend's declared amount is the new shares' cost (entered by you, see "Stock dividends").
- **Source:** `REFERENCES.md` has no row for this cost rule: see `taxjson tax-logic` rules `CA-CORP-07`, `CA-INC-04` and `CA-STKDIV-01`.
- **Rule ids:** `CA-CORP-07`, `CA-INC-04`, `CA-STKDIV-01`.
- **Code:** `src/taxjson/lib/corp_actions.py` — `_canada_spinoff_deemed_dividend`, `_emit_distribution`; `src/taxjson/lib/brokerages/kraken.py` — `_build_staking_reward`.
- **Edge cases and limits:** a pre-2026 Kraken export has no price on staking rows; the price is filled from Yahoo, and an unpriced row is a validation error (KNOWN_ISSUES "Kraken staking emits `net_amount=0`").

## Return of capital

- **Rule:** a return of capital lowers the ACB (`taxjson roc-sum` totals it against T3 box 42). Received with no shares held, or beyond the ACB, it is a capital gain and the ACB is nil (Schedule 3 shows the gain with no proceeds). A corporation's ROC lowers the ACB on the pay date; a Canadian trust's on its record date when the export prints one. For IB only, a foreign issuer's return of capital (by ISIN) is a dividend (`foreign_return_of_capital = "dividend"`, the default); other brokers always lower the ACB. A basis increase posted after the position was fully sold goes into the next purchase's ACB, with a warning. An ADJUST on a short position changes the cover's gain.
- **Source:** s.53(2)(h)(i.1); s.40(3); T4037 "Adjusted cost base — return of capital". The foreign-issuer reading (`CA-ACB-08`) is stated in `taxjson tax-logic`, which cites s.90(1).
- **Rule ids:** `CA-ACB-06`, `CA-ACB-07`, `CA-ACB-08`, `CA-ACB-09` *(setting: `foreign_return_of_capital = "acb"`)*, `CA-ACB-13`, `CA-ACB-14`, `CA-INC-DATE-ROC`, `CA-INC-DATE-ROC-TRUST`.
- **Code:** `src/taxjson/lib/pipeline.py` — `apply_roc_record_dates`, `apply_trust_roc_record_dates`; `src/taxjson/lib/income_dating.py` — `IncomeRules`, `roc_record_date`; `src/taxjson/lib/country.py` — `foreign_roc_mode`; `src/taxjson/lib/core.py` — `CanadaTaxRules`.
- **Edge cases and limits:** Questrade and RBC rows carry no ISIN, so a US issuer's ROC there stays an ACB reduction — check it by hand (KNOWN_ISSUES "Foreign return of capital is only reclassified for IBKR"). IB prints no record date: a January-paid ROC on a Canadian trust is warned about, with the `.tt` ADJUST pair that moves it to Dec 31.

## Non-cash and reinvested distributions

- **Rule:** a `[[distributions]]` entry in taxjson.toml (a reinvested capital-gains distribution, a late ROC factor) becomes an ACB adjustment sized on the shares held on its record date, in the base currency. A DRIP is the income row plus a purchase of the new units at the amount reinvested (an acquisition for the superficial-loss rule). An RBC "NOTIONAL DISTRIBUTION ADJUSTMENT TO BOOK COST" row raises the ACB. The distribution's income is on the T3/T5 slip; taxjson does not count it.
- **Source:** T4037 "Mutual fund units and reinvested distributions"; s.53(2)(h)(i.1) for a negative factor.
- **Rule ids:** `CA-DIST-01`, `CA-DIST-02`, `CA-DIST-03`.
- **Code:** `src/taxjson/bin/taxjson_apply_distributions.py` — `apply_distributions`, `balance_on`; `src/taxjson/lib/project_tables.py` — `distribution_rows`; `src/taxjson/lib/brokerages/rbc_direct.py` — `_build_book_adjust`.
- **Edge cases and limits:** a US-listed fund's USD factor must be converted by you first; a `0` is a placeholder and is not applied. RBC posts year-end book-cost rows in the following spring, so an export taken earlier misses them (KNOWN_ISSUES "RBC exports by Date miss back-dated year-end book-cost rows"; setting `year_end_posting`). A `[[distributions]]` adjustment dated before an opening snapshot is not cut off (KNOWN_ISSUES "Opening balances").

## Stock dividends

- **Rule:** a stock dividend's new shares enter the pool at $0 cost. Its declared amount (a dividend, and also the new shares' cost) is not in the export: add it as a `[[distributions]]` entry or a `.tt` ADJUST, which books the ACB only; the dividend income is reported from the slip. A taxable run of the year says so until the cost is in. The new shares are an acquisition for the superficial-loss rule. Shares of another class paid as a stock dividend are not booked (UNBOOKED).
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rule `CA-STKDIV-01`.
- **Rule ids:** `CA-STKDIV-01`.
- **Code:** `src/taxjson/lib/core.py` — `STOCK_DIVIDEND`, `is_stock_dividend`; `src/taxjson/lib/country.py` — `stock_dividend_zero_cost`, `stock_dividend_in_loss_window`.
- **Edge cases and limits:** a stock dividend received while short is booked as a $0 purchase that covers part of the short (KNOWN_ISSUES "Stock dividends: $0 in Canada until the declared amount is added").

## Income dating: dividends, trust distributions and ROC record dates

- **Rule:** a corporation's dividend, Canadian or foreign, and a payment in lieu belong to the year paid. A Canadian trust's distribution belongs to the year it became payable: a row the broker calls a distribution on a Canadian issuer is dated by its printed record date (Questrade and RBC "REC mm/dd/yy"). Split-share corporations (a built-in list, ticker.map `SPLITSHARE`), rows whose description says "SPLIT CORP" and issuers in `corporate_distributions` are corporations (pay date). A record date 92 days or more before the pay date is not used. A distribution its record date moves into another year is listed as ATTENTION. Withholding is dated with its payment.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rules `CA-INC-DATE-DIV` (citing s.82(1)), `CA-INC-DATE-TRUST` (citing s.104(13)), `CA-INC-DATE-PIL`, `CA-INC-DATE-ISSUER`.
- **Rule ids:** `CA-INC-DATE-DIV`, `CA-INC-DATE-PIL`, `CA-INC-DATE-TRUST`, `CA-INC-DATE-ISSUER`, `CA-DATE-11`.
- **Code:** `src/taxjson/lib/income_dating.py` — `IncomeRules`, `trust_record_date`, `is_canadian_issuer`, `split_share_roots`, `MAX_RECORD_LEAD_DAYS`; `src/taxjson/lib/markets.py` — `is_split_share_root`.
- **Edge cases and limits:** IB prints no record date and calls a trust's distribution a dividend, so IB rows keep the pay date (owner decision, KNOWN_ISSUES "IB income rows carry no record date"). The exports do not say which Canadian issuer is a trust: every Canadian issuer is treated as one except the split-share list and `corporate_distributions`. The T3 slip is authoritative.

## Payments in lieu of a dividend

- **Rule:** a payment in lieu is ordinary income (no gross-up or credit), except one on a Canadian corporation's share paid by a Canadian dealer (IB Canada, Questrade, RBC Direct), which is deemed a taxable dividend — eligible in the estimate, counted in `taxjson divs-sum`. One on a Canadian trust's unit is ordinary income.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rules `CA-INC-03` and `CA-INC-07` (citing s.260(5)/(5.1)).
- **Rule ids:** `CA-INC-03`, `CA-INC-07`, `CA-INC-DATE-PIL`.
- **Code:** `src/taxjson/lib/income_dating.py` — `pil_is_dividend`; `src/taxjson/bin/taxjson_sum_income.py` — `summarize_income`.
- **Edge cases and limits:** an IB file without its BrokerName header leaves the dealer unknown and the payment ordinary income; a trust unit whose payouts no export calls distributions (IB) cannot be told from a share (KNOWN_ISSUES "Payments in lieu: what the exports cannot say"). The dealer's T5 is authoritative.

## Capital-gains dividends (T5 box 18)

- **Rule:** a split-share or mutual-fund corporation's capital-gains dividend is a capital gain, not a dividend. No export labels it, so list it in `[[capital_gains_dividends]]`: `taxjson divs-sum` shows it apart, the estimate taxes it as a capital gain (50% inclusion, no gross-up or credit), and `taxjson carryover` adds it to its year's net capital gain. ACB is unchanged.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rule `CA-INC-06` (citing s.130.1(4)/s.131(1)).
- **Rule ids:** `CA-INC-06`.
- **Code:** `src/taxjson/lib/cg_dividends.py` — `entries_from_config`, `allocate`; `src/taxjson/lib/tax_estimate.py` — `estimate_canada`.
- **Edge cases and limits:** a bare root covers only its Canadian listings, never a preferred series or a foreign listing; an entry that matches no dividend stops the view naming the entry.

## Cryptocurrency

- **Rule:** each coin is its own property; a coin-for-coin trade is a sale of one and a purchase of the other at fair value. Kraken's wallet suffixes and bonded-staking codes are the bare coin; any other code is its own coin unless ticker.map folds it (`GLOBAL CODE COIN`, e.g. `GLOBAL ETH2 ETH`). A Kraken dust sweep is a sale of each coin, the receipt split by `amountusd`. USD stablecoins (built-in list, ticker.map `STABLE`) are US-dollar cash, an approximation. A Kraken fee paid in a coin is a sale of that coin; a trade fee taken in a coin changes the coins bought or sold. Moving coins between your own wallets is not a sale; a send that arrives on another of your exchanges within 10 minutes before to 3 days after with 90–100% of the coins is your own move; any other send is recorded by `taxjson crypto-sends` as `self`, `gift` or `payment`, and a gift or payment is a sale at fair value. A send that arrives short with no stated fee paid the network fee in coins: a sale of them. Staking rewards are income at fair value. Crypto settles on the trade date and is dated in `local_timezone`. Any residue of a coin stays property with its share of cost.
- **Source:** CRA *Guide for cryptocurrency users and tax professionals*; s.47 (identical-property and superficial-loss rules per coin); gifts: s.69(1)(b); staking: s.3, s.9.
- **Rule ids:** `CA-CRYPTO-01` … `CA-CRYPTO-11`, `CA-SL-13`, `CA-DATE-07`, `CA-DATE-12`, `CA-INC-04`.
- **Code:** `src/taxjson/lib/brokerages/kraken.py` — `KrakenBrokerage`, `_fee_coin_sale`, `_build_instant_trades`; `src/taxjson/lib/brokerages/coinbase.py` — `CoinbaseBrokerage`; `src/taxjson/lib/crypto_sends.py` — `match_transfers`, `network_fees`, `fair_value`, `usd_pool`, `is_cash_stablecoin`; `src/taxjson/lib/markets.py` — `is_usd_stablecoin`, `crypto_alias`; `src/taxjson/lib/brokerages/_crypto_common.py` — `utc_to_local`; `src/taxjson/bin/fill_crypto_prices.py`.
- **Edge cases and limits:** `local_timezone` has no default: a project with a crypto account stops until it is set, and changing it re-dates rows and re-keys sends. A stablecoin's own gain or loss (a de-peg) is not computed; a fill more than 2% off 1.00 USD is warned about. A stablecoin gift is a currency disposition, its loss treated as superficial in full when US dollars or stablecoins were acquired within 30 days and are still held (`CA-CRYPTO-08`). Kraken fiat conversions are not modelled; Kraken fees taken in the traded coin are not in the fee reports (KNOWN_ISSUES). Sends pair only across crypto accounts' sidecars. A coin with no `CRYPTO` line is priced as `SYMBOL-USD` on Yahoo; there are no built-in coin ids.

## FX gains on foreign cash

- **Rule:** gains on holding foreign cash are not in the Schedule 3 totals: `taxjson fx-cash` estimates the year's net gain or loss beyond the $200 annual exemption from a pooled average cost per currency (`fx_cash_gains = true` also prints it at the end of `taxjson run`). Cash moves on a trade for cash, income, withholding, fees and the cash a corporate action pays; a share-for-share exchange, a coin swap or a fee in a coin moves none.
- **Source:** s.39(1.1) (individuals, since 2016; formerly s.39(2)); **IT-95R** *Foreign Exchange Gains and Losses*.
- **Rule ids:** `CA-FX-07`.
- **Code:** `src/taxjson/bin/taxjson_fx_cash.py` — `build_ledger`, `apply_jurisdiction`, `_non_cash`, `CA_EXEMPTION`.
- **Edge cases and limits:** IB `Trades / Forex` conversions and Kraken fiat conversions are not read, so an IB account's ledger can overdraft (KNOWN_ISSUES "IB `Trades / Forex` conversions are not modeled"). The estimate leaves the FX result out (KNOWN_ISSUES "Estimate and instalments: FX on cash").

## Shares with an unknown cost: missing history, transfers in, opening balances

- **Rule:** shares sold with no purchase in your files go in `missing_history.json`: sales that draw on them have an unknown cost, are listed for manual reporting and left out of the totals, with no superficial-loss test, until the position is fully sold; a loss near such a sale is flagged for a manual check. A TRANSFER row that reaches a taxable account's books stops the run until the original purchase is declared (`.tt` ACQUIRED). A broker's transfer rows in a taxable account stay out of the books; shares arriving from outside your books take the book value the broker states on the row (Questrade, RBC) as their ACB on the arrival date, said as ATTENTION; a market value (IB) is never a cost. A broker's own cost figure is evidence, never booked: `taxjson find-missing-history --write-purchases` drafts `.tt` lines. An opening balance (`taxjson opening`, `.tt` OPENING) sets a position and its cost on the snapshot day: it joins the pool but is never a purchase for the superficial-loss rule, and it replaces the account's earlier rows of that symbol.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rules `CA-ACB-10` … `CA-ACB-12`, `CA-ACB-15`, `CA-ACB-TRANSFER-BV`, `CA-OPEN-01` … `CA-OPEN-03`.
- **Rule ids:** `CA-ACB-10`, `CA-ACB-11`, `CA-ACB-12`, `CA-ACB-15`, `CA-ACB-TRANSFER-BV`, `CA-OPEN-01`, `CA-OPEN-02`, `CA-OPEN-03`.
- **Code:** `src/taxjson/lib/missing_history.py` — `detect_missing_history`, `load_missing_history`, `detect_superficial_loss_warnings`, `format_purchase_drafts`; `src/taxjson/lib/transfer_in.py` — `stated_book_value`, `arrivals`, `mark_covered`; `src/taxjson/lib/opening.py` — `apply_opening_cutoff`; `src/taxjson/bin/taxjson_convert_tt.py` — `expand_acquired`, `parse_opening_line`; `src/taxjson/bin/taxjson_run.py` — `stage_transfer_arrivals`, `cmd_opening`, `cmd_find_missing_history`.
- **Edge cases and limits:** a broker's book value may leave out a superficial loss, a ROC or the same shares elsewhere: `taxjson sanity` compares costs (KNOWN_ISSUES "Transfers into taxable accounts stay out of the books"). IB's Basis is the cost of the lots IB closed (FIFO), not your ACB. A missing-history opening has no cost, so T1135's cost test can understate (KNOWN_ISSUES "T1135 cost amounts follow the books"). An opening cost in another currency is converted at the snapshot day's rate.

## T1135 foreign property

- **Rule:** `taxjson t1135`: Form T1135 is required when the total cost of specified foreign property in taxable accounts exceeds $100,000 at any time in the year; below $250,000 throughout the simplified method is available. The holdings are walked on the project's tax_date basis. Country comes from the listing suffix; ticker.map `T1135 SYMBOL COUNTRY` overrides it (an ISO 3166 alpha-3 code, or CA/CAN/CANADA/EXCLUDE). Crypto on an exchange counts. A cost amount is the ACB as the engine computes it day by day (superficial-loss additions and option folds included; futures have none).
- **Source:** s.233.3; Form **T1135** and its guide.
- **Rule ids:** `CA-RPT-01`, `CA-RPT-02`, `CA-RPT-12`, `CA-RPT-13`, `CA-RPT-15`.
- **Code:** `src/taxjson/bin/taxjson_t1135.py` — `walk_costs`, `build_report`, `classify_country`, `FILING_THRESHOLD`, `DETAILED_THRESHOLD`; `src/taxjson/lib/t1135_country.py` — `parse_country`.
- **Edge cases and limits:** the test covers these books only; foreign bank accounts, cash and shares held elsewhere add to the same $100,000 and are not seen (KNOWN_ISSUES "T1135 sees only the brokerage books"). A foreign listing whose rows carry a Canadian ISIN is named for a `T1135 SYMBOL CA` line.

## Net capital losses: carryforward and carry-back

- **Rule:** `taxjson carryover` keeps the net-capital-loss ledger in 100% amounts: a loss carries forward with no time limit and back up to 3 years (T1A). Each year is recomputed with the project's settings; an earlier year with a close-year lock takes the lock's filed gain. In the estimate, carried-forward losses are netted against the year's gains before the inclusion and only up to them. `taxjson close-year` records the losses carried in, created, applied and carried out; `taxjson handoff` flags a next-year input that differs.
- **Source:** s.111(1)(b); T4037 "Applying net capital losses".
- **Rule ids:** `CA-RPT-10`, `CA-EST-LOSSES`, `CA-CARRY-01` … `CA-CARRY-05`, `CA-RPT-08`, `CA-RPT-09`.
- **Code:** `src/taxjson/bin/taxjson_carryover.py` — `build_canada_ledger`, `lock_figure`, `load_claimed`; `src/taxjson/lib/carryforward.py` — `resolve_losses`, `record_block`, `handoff_issues`; `src/taxjson/lib/handoff.py`.
- **Edge cases and limits:** a pre-2001 loss is not rescaled for its old inclusion rate (KNOWN_ISSUES "Carryover has no inclusion-rate adjustment"). Losses actually applied on filed returns go in `[carryover] claimed`.

## Alternative minimum tax and the minimum tax carryover

- **Rule:** `taxjson amt` shows the year's minimum tax line by line (2024+ regime: 20.5% over the basic exemption, gains at 100%, dividends at their actual amount, the BPA credit at 50%) and the provincial AMT (ON, BC, AB factors). A year whose federal minimum tax exceeds regular federal tax creates a carryover equal to the excess; it can be applied only in the 7 following years, oldest first, up to regular federal tax minus minimum tax. The carryover by year of origin is read from `[estimate] amt_carryover`, else from the latest close-year lock.
- **Source:** s.127.5–127.55 (as amended 2024); provincial: ON428 worksheet 5006-D, *BC Income Tax Act* s.4.8, AB428 line 67. The carryover rule is stated in `taxjson tax-logic` (`CA-AMT-02` … `CA-AMT-07`, citing s.120.2).
- **Rule ids:** `CA-AMT-01` … `CA-AMT-08`, `CA-EST-AMT`.
- **Code:** `src/taxjson/lib/tax_estimate.py` — `_amt_canada`, `ca_amt_carryover`, `ca_amt_exemption`, `CA_AMT_CARRY_YEARS`; `src/taxjson/lib/amt_report.py` — `build`, `render`; `src/taxjson/lib/carryforward.py` — `resolve_amt`, `amt_from_config`; `src/taxjson/bin/taxjson_run.py` — `cmd_amt`.
- **Edge cases and limits:** a year before 2024 runs on the 2024 tables and is flagged (the old 15% AMT is not modelled). The stock-option deduction add-back, donated securities and credits other than the BPA are not modelled, so AMT can bind when the check says it does not (KNOWN_ISSUES "Estimate: credits, OAS recovery tax and AMT adjustments").

## Instalments and prescribed interest

- **Rule:** `taxjson instalments`: instalments are required when net tax owing exceeds $3,000 this year and in one of the two previous years; due March, June, September and December 15 (next business day on a weekend), on the current-year, prior-year or CRA-reminder basis. Interest at CRA's prescribed rate on each due date's least cumulative requirement, less credit on payments (the offset method; $25 or less not charged); credit interest only offsets. The s.163.1 penalty is 50% of net interest over the greater of $1,000 and 25% of the no-payment interest. Without a configured rate the built-in quarterly table is used (the last rate carried forward). Interest runs to April 30 of the next year.
- **Source:** s.156(1), s.161(2), s.161(4.01); CRA "instalment interest and penalty charges"; prescribed rate: s.161(2), Reg. 4301(a).
- **Rule ids:** `CA-RPT-11`, `CA-INST-PRIOR`, `CA-INST-INTEREST`, `CA-INST-PENALTY`, `CA-INST-RATES`, `CA-INST-DUE`.
- **Code:** `src/taxjson/bin/taxjson_instalments.py` — `due_dates`, `least_cumulative_schedule`, `interest_and_penalty`, `PUBLISHED_RATES`, `THRESHOLD`, `BASES`; `src/taxjson/bin/taxjson_run.py` — `cmd_instalments`.
- **Edge cases and limits:** a year whose net tax is not given is assumed to meet the prior-year test, so instalments are reported as required. A payment dated before January 1 counts only with `tax_year = YEAR` on its row. Self-employment CPP/EI is not an input (KNOWN_ISSUES "Estimate and instalments"). US estimated tax (1040-ES) is not modelled.

## Tax estimate (planning only)

- **Rule:** `taxjson estimate`: federal and provincial tax (ON, BC, AB) on top of your other income, with AMT, for planning only. Canadian dividends are treated as eligible (38% gross-up and credit), a Canadian trust's distribution with them; foreign dividends as ordinary income with withholding credited up to 15%; interest is left out. The federal BPA phases down on net income; it is the only non-refundable credit modelled. Rates are the tax year's own table, else the nearest earlier one (said).
- **Source:** eligible dividends s.82(1)(b)(ii), s.121(b); foreign dividends s.126(1), Form T2209; BPA s.118(1)(c), (1.1); brackets s.117 and the provincial acts (see `REFERENCES.md` "Canada — income and the tax estimate").
- **Rule ids:** `CA-RPT-03` … `CA-RPT-06`, `CA-EST-TRUST`, `CA-EST-DEDUCT`, `CA-EST-BPA`, `CA-EST-PROV`, `CA-EST-FTC`, `CA-EST-VINTAGE`, `CA-EST-FED-TABLE`, `CA-EST-DTC`, `CA-EST-ON-TABLE`, `CA-EST-BC-TABLE`, `CA-EST-AB-TABLE`, `CA-EST-OHP`, `CA-INC-01`, `CA-INC-02`, `CA-INC-05`, `CA-RPT-16`, `CA-SCAN-01`, `CA-SCAN-02`.
- **Code:** `src/taxjson/lib/tax_estimate.py` — `estimate_canada`, `_canada_tax`, `ca_fed_bpa`, `ontario_health_premium`, `_VINTAGES`, `CA_PROVINCES`; `src/taxjson/lib/trade_stats.py`.
- **Edge cases and limits:** non-eligible dividends are estimated as eligible; Quebec and other provinces are refused; OAS recovery tax is not modelled; dividends are classified by listing suffix when the books carry no ISIN (KNOWN_ISSUES "Non-eligible dividends are estimated as eligible", "Estimate classifies dividends by listing suffix"). `taxjson stats` is a view, not a filing number.

## Capital or income account (scope)

- **Rule:** every security, short sales and written options included, is assumed to be on capital account; short records carry `direction = SHORT` so they can be moved. Whether trading is an adventure in the nature of trade is the user's question.
- **Source:** s.39(4); **IT-479R** paras 9–20 (and paras 18, 25(c)).
- **Rule ids:** none (scope, stated in `REFERENCES.md` and `README.md`).
- **Code:** `src/taxjson/lib/core.py` — `CanadaTaxRules`.
- **Edge cases and limits:** CRA's stated position is that short-sale gains are on income account; taxjson does not take that position.

## Project country (partition)

- **Rule:** the project's country is required (`country = "canada"`); taxjson never assumes one. US-only settings, commands, flags and account plans are refused in a Canada project. Books built under the other country are refused by every report until `taxjson run` rebuilds them. `base_currency` must be CAD.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rules `CA-CTRY-01` … `CA-CTRY-03`.
- **Rule ids:** `CA-CTRY-01`, `CA-CTRY-02`, `CA-CTRY-03`.
- **Code:** `src/taxjson/lib/country.py` — `settings_country`, `config_country_problems`, `SETTING_COUNTRY`, `COMMAND_COUNTRY`, `PLAN_COUNTRY`; `src/taxjson/lib/config_check.py` — `settings_problems`.

---

# Part 2 — United States (experimental)

## Tax year: the trade date

- **Rule:** a trade belongs to the year it is traded (`tax_date = "trade"`, the default). Settle dates follow the same cycles and calendars as in Canada, and a Webull row's printed settle date is walked back one cycle to the trade date. Interest, dividends and payments in lieu belong to the year paid (January fund dividends aside). Crypto is dated in `local_timezone`.
- **Source:** `REFERENCES.md` has no row for the US date basis: see `taxjson tax-logic` rule `US-DATE-01`.
- **Rule ids:** `US-DATE-01`, `US-DATE-02` *(setting: `tax_date = "settle"`)*, `US-DATE-03` … `US-DATE-11`, `US-DATE-12` *(setting: `futures_settle = "next_day"`)*, `US-DATE-13` … `US-DATE-17`, `US-DATE-SESSION`, `US-INC-DATE-DIV`.
- **Code:** `src/taxjson/lib/dates.py` — `settlement_date`, `market_of`; `src/taxjson/lib/country.py` — `resolve_tax_date`, `DEFAULT_TAX_DATE`; `src/taxjson/lib/corporate_timeline.py` — `event_sort_key`, `UsPriority`.
- **Edge cases and limits:** rows of one account at one moment from two files follow the files' name order, which decides which lot FIFO takes (`US-DATE-17`).

## Currency

- **Rule:** amounts are in USD; other currencies are converted at the Yahoo Finance daily rate for the settle date, a rate up to 12 days old used across gaps, and a row with none stops the run. `base_currency` must be USD.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rules `US-FX-01`, `US-FX-02`.
- **Rule ids:** `US-FX-01`, `US-FX-02`, `US-CTRY-03`.
- **Code:** `src/taxjson/bin/to_base_curr.py` — `fetch_yahoo`, `build_rates`.

## Basis: FIFO lots per account

- **Rule:** first in, first out per account; specific-lot identification is not supported. Purchase commissions add to basis; sale commissions reduce proceeds; a commission refunded later nets against its trade. With `transfers = false` a move between two of your own taxable accounts is not a sale: the sending account's lots go to the receiving account with their basis and purchase dates. Retirement accounts (`type = "sheltered"`) are kept out of Form 8949 but count for wash sales.
- **Source:** Reg. §1.1012-1(c) (FIFO only).
- **Rule ids:** `US-BASIS-01`, `US-BASIS-02`, `US-BASIS-03`, `US-BASIS-COMMREFUND`, `US-BASIS-05`, `US-BASIS-07`.
- **Code:** `src/taxjson/lib/core.py` — `USATaxRules`, `_draw_long_lots`, `_move_long_lots`; `src/taxjson/bin/taxjson_run.py` — `stage_own_account_moves`, `stage_blended_wash_pass`.
- **Edge cases and limits:** a broker 1099-B computed under specific ID will not reconcile per lot (KNOWN_ISSUES "US: specific-lot identification is not supported"). Out and in rows that look like a move but do not pair are ATTENTION lines; those shares' sales are reported by hand.

## Holding period

- **Rule:** long-term when held more than one year, otherwise short-term; an acquisition on the last day of a month is long-term from the first day of the corresponding month a year later. A stand-alone short sale is short-term. A wash-sale replacement takes the loss shares' holding period.
- **Source:** §1222; **Rev. Rul. 66-7**; §1223(3) (tacking).
- **Rule ids:** `US-HOLD-01`, `US-HOLD-02`, `US-HOLD-03`, `US-WASH-10`.
- **Code:** `src/taxjson/lib/core.py` — `held_more_than_one_year`, `USATaxRules`.
- **Edge cases and limits:** only the matched shares of a replacement lot carry the tacked holding period (KNOWN_ISSUES "§1223(3) tacking is per-share").

## Identical security: listings, renames and broker codes

- **Rule:** as in Canada, a security is its symbol with its listing suffix; two listings are one security only when ticker.map, a `.tt` JOURNAL line (your declaration: no sale, the lots keep their basis and holding periods; one root or agreeing names, as in Canada, else a `TOBASE` line) or an exact-name journal pair joins them (an ADR and its ordinary shares are never joined automatically); a broker's explicit journal compares its two legs' own names, as in Canada. A Questrade or RBC ticker's listing is read from the evidence as in Canada. A dated rename (a `.tt` RENAME line, a broker row, IB's one contract id under two symbols) carries lots and holding periods; the wash-sale rule treats OLD before and NEW after as one security. A broker's internal code is resolved the same way as in Canada. A Canadian broker's currency journal (Questrade BRW) is not expected in a US project: its legs are ordinary transfer legs, never joined on the broker's pairing alone.
- **Source:** `REFERENCES.md` has no US row: see `taxjson tax-logic` rules `US-BASIS-06`, `US-XLIST-01`, `US-BASIS-RENAME`, `US-BASIS-CODES`.
- **Rule ids:** `US-BASIS-06`, `US-XLIST-01`, `US-XLIST-02`, `US-XLIST-03`, `US-BASIS-RENAME`, `US-BASIS-CODES`, `US-CORP-02`.
- **Code:** `src/taxjson/lib/cross_listings.py` — `analyze`; `src/taxjson/lib/listing_suffix.py` — `resolve`; `src/taxjson/lib/symbol_codes.py` — `resolve`; `src/taxjson/lib/renames.py` — `apply_dated_renames`; `src/taxjson/lib/dated_events.py` — `read_declarations`; `src/taxjson/bin/taxjson_run.py` — `stage_cross_listings`, `stage_dated_events`.

## Wash sales (§1091)

- **Rule:** a loss is disallowed when the same security is bought within 30 days before or after the sale (trade dates), in any of your accounts, share for share; the identical option is a replacement too; re-shorting after a short-cover loss counts. There is no still-held test, but shares closed by the same sale never replace each other, and a purchase in the selling account replaces only with shares still unsold at the loss. A purchase in another taxable account replaces even if that account sold it before the loss (its earlier sale's gain falls; a filed year is left as filed, with an ATTENTION). Replacements are matched in acquisition order, losses in sale order. The disallowed loss is added to the replacement's basis. A long call bought in the window is a warning only; warrants, adjusted series and futures options are flagged. Look-alike securities are not detected.
- **Source:** **IRC §1091**; Treas. Reg. **§1.1091-1**; §1223(3); **Publication 550** "Wash Sales".
- **Rule ids:** `US-WASH-01` … `US-WASH-10`, `US-WASH-12`, `US-WASH-14`, `US-WASH-15`, `US-WASH-17`, `US-WASH-19` … `US-WASH-22`, `US-RPT-05`, `US-PLAN-01`, `US-PLAN-02`, `US-PLAN-04`.
- **Code:** `src/taxjson/lib/core.py` — `USATaxRules`, `find_replacements_in_window`, `_sold_replacements_in_window`, `detect_option_replacement_matches`; `src/taxjson/lib/pipeline.py` — `place_retro_wash_adjustments`; `src/taxjson/bin/taxjson_wash_radar.py` — `_us_engine_losses`.
- **Edge cases and limits:** a long SALE within the window of a short-cover loss does not disallow it (§1091(e)(1) not modelled, `US-WASH-19`). A replacement bought and sold in the loss's own account before the loss does not wash it. Options as replacement property are advisory only: treat the warning as an instruction (KNOWN_ISSUES "US: options as replacement property are advisory-only"). Matching across accounts needs a full `taxjson run`.

## IRA and affiliated replacements

- **Rule:** a replacement bought in an IRA makes the loss permanently disallowed, with no basis adjustment, even when the IRA sold it again before the loss. A spouse's or controlled corporation's purchase in the window disallows the loss too when their trades are given (declare their account `type = "sheltered"`); the loss goes to the basis of their shares, so in your books it is permanent.
- **Source:** **Rev. Rul. 2008-5** (IRA repurchases); IRC §1091(d) for the affiliated case is stated in `taxjson tax-logic` rule `US-WASH-16`.
- **Rule ids:** `US-WASH-04`, `US-WASH-11`, `US-WASH-16`.
- **Code:** `src/taxjson/lib/core.py` — `USATaxRules`; `src/taxjson/bin/taxjson_run.py` — `stage_wash_pass`.
- **Edge cases and limits:** `taxjson run` never reads a spouse's account unless you add it (KNOWN_ISSUES "Purchases by an affiliated person").

## Retirement-account transfers

- **Rule:** a retirement account's transfer in or out (a rollover, a custody move) is a move between accounts, never a replacement; the run warns once about each transfer-in near a loss. With `transfers_as_acquisitions = true` such a transfer is a purchase or sale on its date and an arrival in a loss's window stops the run until declared.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rules `US-WASH-23` and `US-WASH-24`.
- **Rule ids:** `US-WASH-23`, `US-WASH-24` *(setting: `transfers_as_acquisitions = true`)*.
- **Code:** `src/taxjson/lib/pipeline.py` — `_handle_transfers`, `transfers_in_loss_windows`.

## In-kind moves to and from retirement accounts

- **Rule:** a taxable account's transfer-out paired with a retirement account's transfer-in of the same security and quantity within 10 days (across brokers; legs of the same kind pair first across the project, so a move between two taxable or two retirement accounts stays a move of your own; a cross-kind pair with another plausible partner is listed NOT booked as ambiguous until an `INKIND` line or `INKIND <date> <symbol> <qty> plan=own` settles it) is a contribution in kind, which an IRA, Roth IRA, 401(k), HSA or 529 does not take (cash only): it is warned about as a likely error and NOT booked — the shares stay in the taxable books (`run --strict` stops). A distribution in kind (the reverse pair) is a purchase by the taxable account at fair market value on the distribution date: its basis, its holding period starting then, a §1091 replacement like any purchase; the taxable amount is on Form 1099-R (noted, not booked). The value: a `.tt` `INKIND` line, else the market value the broker states on the transfer row (IB), else Yahoo's close on the date (ESTIMATED); with `TAXJSON_OFFLINE` and no cached close the run stops and names the line to add.
- **Source:** IRC §219 and §408(a)(1) (IRA contributions in cash); a distribution of property takes its fair market value as basis (Form 1099-R instructions, box 1). `REFERENCES.md` has no row: see `taxjson tax-logic` rules `US-INKIND-01` … `US-INKIND-03`.
- **Rule ids:** `US-INKIND-01` … `US-INKIND-03`.
- **Code:** `src/taxjson/lib/in_kind.py` — `pair_all`, `decide`, `value`, `booked_rows`, `message`; `src/taxjson/lib/country.py` — `in_kind_contribution_booked`; `src/taxjson/bin/taxjson_run.py` — `in_kind_state`, `_say_in_kind`.
- **Edge cases and limits:** a rollover between two retirement accounts is a move of your own (`US-WASH-23`). Only equal quantities pair on their own (a delivery in parts is warned about). The income of a distribution is not booked.

## Options (§1234), exercise, assignment and warrants

- **Rule:** premiums are taxed when the position closes. Exercise or assignment folds the premium into the stock's basis or proceeds; each assignment's premium goes to its own stock leg (same pairing rule as Canada). Cash-settled options realize on the option. Exercising a warrant or right is not a sale: its basis and the exercise price become the shares' basis, and the holding period starts at exercise.
- **Source:** §1234; Pub 550 "Options".
- **Rule ids:** `US-OPT-01`, `US-OPT-02`, `US-OPT-03`, `US-OPT-05`, `US-OPT-06`.
- **Code:** `src/taxjson/lib/core.py` — `USATaxRules`, `_take_option_adj`, `_pair_assign_legs`, `exercise_target`.

## §1256 contracts and futures

- **Rule:** §1256 60/40 and year-end marking are not modelled. A futures contract is booked on its settled P/L, partial closes taken FIFO. Futures, options on futures and broad-based index options (a built-in root list, ticker.map `INDEXOPT`) are kept off Form 8949 and listed with their P/L for Form 6781. A loss on a futures contract is never a wash sale (a re-purchase is flagged). The estimate treats §1256 P/L as short-term and names it.
- **Source:** `REFERENCES.md` has no §1256 row: see `taxjson tax-logic` rules `US-OPT-04`, `US-FUT-01`, `US-FUT-02`, `US-WASH-18`.
- **Rule ids:** `US-OPT-04`, `US-FUT-01`, `US-FUT-02`, `US-WASH-18`, `US-DATE-09`.
- **Code:** `src/taxjson/lib/futures.py` — `section_1256_kind`, `settle_futures`; `src/taxjson/bin/taxjson_form_export.py` — `build_8949`, `filing_6781`, `section_1256_lines`; `src/taxjson/lib/markets.py` — `is_index_option_root`.
- **Edge cases and limits:** report §1256 contracts on Form 6781 from your 1099-B; an index option whose root is not listed is filed as an ordinary option (KNOWN_ISSUES "§1256 (60/40 mark-to-market) is not implemented").

## Mergers: §1001, §368 and §356

- **Rule:** a merger is elected per event: `taxable_exchange` (§1001: sold and bought at FMV), `reorg_368` (all-stock: basis carries over, holding period tacks) or `reorg_368_boot` (§356 per lot: gain recognised up to the lot's share of the cash, a loss never; new basis = basis − cash + gain). Cash in lieu is a sale of the fraction from the oldest lot. A cash merger is a sale; shares-and-cash from an IB row stops the run as UNSUPPORTED.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rules `US-CORP-03` … `US-CORP-05` (citing §1001, §368(a), §358, §1223(1), §356, Reg. §1.356-1(b)).
- **Rule ids:** `US-CORP-01`, `US-CORP-03`, `US-CORP-04`, `US-CORP-05`, `US-CORP-08`, `US-CORP-09`, `US-CORP-10`, `US-CORP-11`.
- **Code:** `src/taxjson/lib/corp_actions.py` — `USA_MERGER`, `_emit_boot_exchange`, `_emit_basis_carryover_rename`, `_emit_taxable_exchange`.
- **Edge cases and limits:** a significant holder attaches the Reg. §1.368-3 statement; taxjson only says so.

## Spin-offs: §355 or §301

- **Rule:** `tax_free_355` moves the US-dollar basis you give (per the company's Form 8937) from the parent; every parent lot gives up the same fraction, each block of spun-off shares keeps its block's purchase date; never a gain (an amount beyond the basis is capped, with ATTENTION). Or `taxable_distribution_301`: income at FMV, which is also the new shares' cost. Spun-off shares are not a wash-sale purchase.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rules `US-CORP-06`, `US-CORP-07` (citing §355, §358(b), Reg. §1.358-2, §1223(1)).
- **Rule ids:** `US-CORP-06`, `US-CORP-07`.
- **Code:** `src/taxjson/lib/corp_actions.py` — `USA_SPINOFF`, `_us_spinoff_tax_free_355`, `_emit_distribution`.

## Return of capital (§301(c))

- **Rule:** a return of capital lowers basis pro rata over the open lots for every issuer; the part beyond a lot's basis is a capital gain in the year received, short- or long-term by the lot. Received with no shares held, or a basis increase with no long shares, is not applied: taxjson warns.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rules `US-ROC-01` … `US-ROC-04` (citing §301(c)(2), §301(c)(3)).
- **Rule ids:** `US-ROC-01`, `US-ROC-02`, `US-ROC-03`, `US-ROC-04`, `US-INC-DATE-ROC`.
- **Code:** `src/taxjson/lib/core.py` — `USATaxRules`, `_inv_keys_for`.

## Stock dividends (§305, §307)

- **Rule:** a stock dividend is not income: basis is spread over old and new shares, the new shares keep the old purchase dates, and they are not a wash-sale purchase. A taxable stock dividend (§305(b)) is not detected. Received with no shares held, the new shares are a $0 purchase with a warning.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rules `US-STKDIV-01` … `US-STKDIV-03`.
- **Rule ids:** `US-STKDIV-01`, `US-STKDIV-02`, `US-STKDIV-03`.
- **Code:** `src/taxjson/lib/core.py` — `USATaxRules`, `is_stock_dividend`; `src/taxjson/lib/country.py` — `stock_dividend_in_loss_window`.

## Non-cash distributions

- **Rule:** `[[distributions]]` entries and RBC notional distributions adjust basis on the shares held on the record date; a DRIP is income plus a purchase (a wash-sale purchase). The income is on Form 1099-DIV.
- **Source:** `REFERENCES.md` has no US row: see `taxjson tax-logic` rules `US-DIST-01` … `US-DIST-03`.
- **Rule ids:** `US-DIST-01`, `US-DIST-02`, `US-DIST-03`.
- **Code:** `src/taxjson/bin/taxjson_apply_distributions.py` — `apply_distributions`.

## Income: payments in lieu and January fund dividends

- **Rule:** a payment in lieu is ordinary, non-qualified income whoever pays it. Dividends are booked gross, withholding as its own TAX row (no foreign tax credit computed). A fund or REIT dividend declared in October–December and paid in January is received Dec 31: taxjson keeps the pay date, warns about a January dividend with an October–December ex or record date, and moves those listed in `ric_january_dividends`. Staking rewards are ordinary income at fair value.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rules `US-INC-01` … `US-INC-03`, `US-INC-DATE-RIC` (citing §852(b)(7), §857(b)(9)).
- **Rule ids:** `US-INC-01`, `US-INC-02`, `US-INC-03`, `US-INC-DATE-RIC`.
- **Code:** `src/taxjson/lib/income_dating.py` — `parse_ric_entries`, `ric_prior_year`, `IncomeRules`.
- **Edge cases and limits:** no export says which payer is a fund (KNOWN_ISSUES "US January fund and REIT dividends need a list"). Form 1099-DIV is authoritative.

## Crypto as property (no wash sale)

- **Rule:** each coin is property; swaps, Kraken dust sweeps, wallet suffixes and `GLOBAL` folds work as in Canada. Accounts marked crypto are not subject to the wash-sale rule. USD stablecoins are property like any coin (a de-peg is a gain or loss); a swap, reward or fee in one is valued at the 1.00 USD par. Under 1e-08 units is zero in the US engine (a row is not booked, a lot residue folds into the closing sale). A move between two of your taxable crypto accounts carries the lots with their basis and dates.
- **Source:** `REFERENCES.md` has no US crypto row: see `taxjson tax-logic` rules `US-CRYPTO-01` … `US-CRYPTO-08`, `US-WASH-13`.
- **Rule ids:** `US-CRYPTO-01` … `US-CRYPTO-08`, `US-WASH-13`, `US-PLAN-05`, `US-DATE-07`, `US-DATE-11`.
- **Code:** `src/taxjson/lib/brokerages/kraken.py` — `KrakenBrokerage`; `src/taxjson/lib/brokerages/coinbase.py` — `CoinbaseBrokerage`; `src/taxjson/lib/crypto_sends.py` — `own_moves`, `match_transfers`; `src/taxjson/lib/core.py` — `non_capital`.
- **Edge cases and limits:** the US lot epsilon is 1e-8 units; each case is named in a warning (KNOWN_ISSUES "Sub-micro quantity tolerance in the US engine").

## Crypto sends: payments and gifts

- **Rule:** paying with crypto is a sale at fair value (`payment`). A gift is not a sale for the donor, so `gift` is refused in a US project (record it as `self`); a gift already saved stops `taxjson run` until it is reclassified. A send that arrived short paid a network fee: a sale.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rules `US-SEND-01`, `US-SEND-02`, `US-CRYPTO-05`.
- **Rule ids:** `US-SEND-01`, `US-SEND-02`.
- **Code:** `src/taxjson/lib/crypto_sends.py` — `REFUSED`, `refused_entries`, `network_fees`; `src/taxjson/bin/taxjson_run.py` — `_stage_crypto_sends`.

## FX gains on foreign cash (§988)

- **Rule:** gains on holding foreign cash are ordinary income, not capital gains, and not in the Form 8949 totals; `taxjson fx-cash` estimates them (`fx_cash_gains = true` runs it after `taxjson run`). The §988(e) personal exclusion is not modelled and there is no $200 exemption.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rule `US-FX-03`.
- **Rule ids:** `US-FX-03`.
- **Code:** `src/taxjson/bin/taxjson_fx_cash.py` — `apply_jurisdiction`.

## Shares with an unknown basis: missing history, transfers in, opening balances

- **Rule:** as in Canada, shares sold with no purchase go in `missing_history.json` and their sales are reported by hand; a loss near one is flagged for a manual wash-sale check (trade dates). A transfer-in from outside your books takes the basis the broker states, as one lot dated the arrival (no §1223 tacking): enter the original lots for long-term treatment. An opening balance is one line per lot with its real purchase date (a line without one stops the run) and its cost in US dollars.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rules `US-BASIS-04`, `US-BASIS-08`, `US-BASIS-TRANSFER-BV`, `US-OPEN-01` … `US-OPEN-03`.
- **Rule ids:** `US-BASIS-04`, `US-BASIS-08`, `US-BASIS-TRANSFER-BV`, `US-OPEN-01`, `US-OPEN-02`, `US-OPEN-03`.
- **Code:** `src/taxjson/lib/missing_history.py` — `detect_missing_history`; `src/taxjson/lib/transfer_in.py` — `stated_book_value`; `src/taxjson/lib/opening.py` — `apply_opening_cutoff`.
- **Edge cases and limits:** IB's Open Positions Lot rows are not read, so a US opening from an IB statement has no lot dates: list the lots in a holdings TOML with `acquired` (KNOWN_ISSUES "Positions reports: what is not read").

## Form 8949 boxes and exports

- **Rule:** `taxjson form-export --form 8949`: Part I short-term, Part II long-term, wash sales as code W. From tax year 2025 a crypto account's dispositions are digital assets on boxes G/H/I and J/K/L with their own totals; securities stay on A–F. `--form txf` writes the securities rows only. Cells are rounded half-up and (h) = (d) − (e) + (g) on the rounded cells. The checklist names Form 1099-B and, from 2025, Form 1099-DA.
- **Source:** Form 8949 instructions.
- **Rule ids:** `US-RPT-01`, `US-RPT-02`, `US-RPT-03`, `US-RPT-09`, `US-RPT-10`, `US-RPT-11`, `US-RPT-06`, `US-RPT-12`.
- **Code:** `src/taxjson/bin/taxjson_form_export.py` — `build_8949`, `build_txf`, `DIGITAL_ASSET_BOXES_FROM`, `filing_parts_8949`; `src/taxjson/lib/checklist.py` — `_slip_names`.

## Capital loss carryover

- **Rule:** `taxjson carryover` keeps the short- and long-term carryover (Schedule D worksheet), assuming the $3,000 ordinary offset is used each year unless `[carryover] claimed` says otherwise; a carryover keeps its term. `taxjson close-year` records it; `taxjson handoff` flags a differing next-year input.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rules `US-RPT-08`, `US-CARRY-01` … `US-CARRY-04`, `US-EST-CARRY-TERM`, `US-EST-CARRY-TI`.
- **Rule ids:** `US-RPT-08`, `US-CARRY-01`, `US-CARRY-02`, `US-CARRY-03`, `US-CARRY-04`, `US-EST-CARRY-TERM`, `US-EST-CARRY-TI`.
- **Code:** `src/taxjson/bin/taxjson_carryover.py` — `build_usa_ledger`, `lock_figure`; `src/taxjson/lib/tax_estimate.py` — `estimate_usa`.

## Tax estimate; what is not modelled

- **Rule:** `taxjson estimate`: federal tax only (single filer, standard deduction), every dividend qualified, NIIT 3.8% over the MAGI threshold, a net capital loss offsetting up to $3,000. AMT (Form 6251) and its credit are not modelled; there is no `taxjson amt` in a US project; estimated tax (1040-ES) is not modelled.
- **Source:** qualified dividends §1(h)(11); NIIT §1411.
- **Rule ids:** `US-RPT-04`, `US-RPT-07`, `US-EST-NIIT`, `US-EST-NIIT-LOSS`, `US-EST-VINTAGE`, `US-EST-TABLE`, `US-AMT-01`.
- **Code:** `src/taxjson/lib/tax_estimate.py` — `estimate_usa`, `_usa_tax`, `US_NIIT_RATE`.
- **Edge cases and limits:** KNOWN_ISSUES "US AMT is not computed", "US estimated taxes (1040-ES) are not modeled", "`taxjson sum` tax estimate assumes dividend classification".

## Project country (partition)

- **Rule:** `country = "usa"` is required; Canada-only settings, tables, commands (`taxjson amt`, `taxjson instalments`, `taxjson t1135`, `taxjson option-boundary`, a `crypto-sends` gift, Schedule 3 export), flags and plans are refused. `base_currency` must be USD.
- **Source:** `REFERENCES.md` has no row: see `taxjson tax-logic` rules `US-CTRY-01` … `US-CTRY-03`.
- **Rule ids:** `US-CTRY-01`, `US-CTRY-02`, `US-CTRY-03`.
- **Code:** `src/taxjson/lib/country.py` — `config_country_problems`, `command_country_problem`, `CONFIG_COUNTRY`.
