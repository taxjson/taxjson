# Design-Level Audit — 2026-07-04

Six parallel subsystem reviews (core engine, pipeline orchestrator, brokerage parsers,
corp-actions, reporting/web, product/domain + cross-cutting). Unlike the five prior
correctness audits, this pass targets architecture, awkward seams, feature gaps, and
maintainability. Every reviewer was seeded with the exclusion lists
(`audit_open_issues_2026_07`, `known_limitations`, `KNOWN_ISSUES.md`); nothing below
re-reports a known/fixed/accepted item.

**The through-line across all six reports:** the recorded bug history is dominated by
*agreement failures between hand-maintained copies of the same concept* — split/rename
math implemented ~7 times, event ordering defined 5 ways, JSON loading/aggregation
duplicated ~10 times in the report layer, the transaction schema living only in comments
and an LLM prompt. Nearly every structural recommendation converts one implicit
multi-site agreement into one explicit, testable definition.

---

## Report 1 — Core tax engine (core.py, phantom_holdings.py, numeric.py, trace_format.py, taxjson_gains.py)

1. **Split/rename arithmetic exists in ~7 independent implementations** — this is *why* the
   split×wash bug class kept regenerating across audits. Sites: `_dedupe_corporate_splits`
   core.py:261-292; Canada `sym_alias` union-find :395-412; `_window_splits`+`_to_loss_units`
   :961-983 (defined inside the per-loss loop); Canada SPLIT pool branch :580-656; US
   `split_schedule` :1578-1601 + `_rep_units_factor` :1698-1718; phantom_holdings
   `_drop_duplicate_splits` ph:52-71, rename migration ph:127-146, `synthesize_openings`
   chain walk ph:606-664. **Fix:** extract a `SplitTimeline` class
   (`lib/corporate_timeline.py`) with `canonical()`, `factor(sym, from, to]`, `dedupe()`,
   `splits_between()`; all engines + phantom walks + radar consume it. Effort M. Highest
   bug-class ROI in the repo.

2. **Half the gains pipeline lives in the CLI** — TRANSFER strip, phantom synthesis, year
   filtering, by_ticker rebuild, taint split, warnings all live only in
   `taxjson_gains.py main()` (cli:371-696), so explain/web drift by construction (the
   tracked divergences were symptoms). **Fix:** `lib/pipeline.py` with `run_gains(txs,
   sheltered, affiliated, GainsRequest)`; main()/explain/web all call it. Effort M.

3. **Two ~1,100-line god-methods with logic trapped in closures** — Canada compute_gains
   core.py:300-1437; US :1480-2509. `make_gain_entry` is redefined per-transaction
   (:1859-1902 inside the tx loop). No unit-test seams: all 27 engine test files must
   build full scenarios. **Fix:** hoist decision functions to module level in dependency
   order: SplitTimeline → event_sort_key → WashWindow policy object → shared balance walk
   (:426-443 and :943-950 are near-copies). Effort L, incremental.

4. **Dict-shaped mutable records with identity-based links.** Wash-deferral integrity
   hangs on `r.get('short_lot_ref') is short_lot` (core.py:1944, :2196) with live
   mutation through aliases (:2040, :2276) — copying a lot silently severs the link and
   the disallowance evaporates. Long/short lots have different key sets (:1831-1837
   comment admits the KeyError hazard). Wash-record shapes diverge between engines
   (nested `loss_tx` vs flat), forcing the CLI's `w.get('loss_tx', w)` hack.
   **Fix:** dataclasses (`Lot`, `Pool`, `Replacement`, `GainEntry`, `WashRecord`),
   `lot_id` registry instead of object identity, unified wash-record shape. Effort M.

5. **Five different sort keys define event ordering** — Canada main pass
   `(sort_date, phase, time, priority)` :455; Canada balance walk (no priority) :428-431;
   US `(sort_date, time, priority)` — priority *after* time — :1541-1544 (different
   ordering philosophy from Canada); phantom walks `(date, time)` with no priority at all
   (ph:107-109 etc.), which is why synthesize_openings anchors the OB a day early as a
   workaround (ph:685-699). **Fix:** one exported `event_sort_key(tx, basis=...)` with
   the ladder as an IntEnum; used by both engines and all walks. Effort S-M.

6. **Stringly-typed actions, classification sets copy-pasted ~8 times** and not all
   identical (core.py:496, :1516, :1668, :1083; ph:115, :224, :307, :652; cli:501-502).
   **Fix:** `Action(StrEnum)` + named frozensets. Effort S.

7. **Content-hash identity + mutable records + deliberate id collisions.** Solver mutates
   records after id computed (:1003-1004); DISALLOW vtx reuses the loss tx's id (:1016).
   **Fix:** namespaced vtx ids (`DISALLOW_<loss_id>`), freeze the dataclass or invalidate
   id on mutation. Effort S-M.

8. **Missing conservation post-conditions** — share-count conservation (replay signed
   qtys through the split timeline vs reported inventory), basis conservation, and
   pending-adjustment drainage (`pending_adjustments` :461, `pending_option_adjustments`
   :1684 non-empty at end ⇒ warn). Would have caught most historical engine regressions
   in one assert. **Fix:** `verify_conservation()` at end of both engines → DIAGNOSTICS.
   Effort S-M.

9. **Numeric policy: sound core, ragged edges.** Epsilon zoo (19× 1e-6, 4× 1e-9, US-only
   1e-8 :1499 — Canada and US disagree on what "zero qty" is); per-chunk gain math is
   float composition (:991, :2205); `_QTY_FIELDS` (numeric.py:46-53) is a stringly-typed
   rounding contract with engine output keys. **Fix:** named constants; schema-driven
   rounding when records become dataclasses. Effort S.

10. **Traces are pre-rendered strings; the engine regex-parses its own trace output**
    (core.py:1170-1179 — and the `f"| {v.symbol}"` substring test matches symbol
    prefixes). 24 `if trace:` sites interleave formatting with tax logic. **Fix:**
    structured TraceEvent stream; render in trace_format.py. Doubles as the per-lot
    audit-trail feature. Effort M.

11. **US lot selection hardcoded FIFO** (`pop(0)` :1937, :2189). **Fix:** `LotSelector`
    strategy hook (FIFO / specific-ID sidecar / average-cost symbol set). Effort M,
    after dataclasses.

12. **Misc:** dead `cross_asset` param (all 3 signatures, never read — but see A1 below:
    wire it, don't delete); income emission duplicated verbatim (:1341-1389 vs
    :2389-2427); by_ticker built 3×; stdin loader is a cruder second copy of
    load_transactions (cli:332-350); `inspect.signature` recomputed per call (:160-162).

13. **SettlementCalendar protocol** — one landing site for future holiday data instead of
    another cross-cutting audit. Effort S (hook only).

---

## Report 2 — Pipeline orchestrator (taxjson_run.py + stages)

1. **The stage graph is real but implicit** — ~10 hand-written `needs_rebuild(out, *deps)`
   sites where dep lists are maintained separately from the argv that consumes them
   (e.g. :574 vs :567-573); `stage_account` is 290 lines mixing cache policy, argv
   construction, printing, naming conventions. **Fix:** declarative `Stage` table
   (inputs derived from argv) + toposort executor; `--dry-run`, `--explain-cache`,
   timing, parallel accounts, uniform diag policy all become executor properties.
   Also split run.py: orchestrator vs ~1,400 lines of query subcommands. Effort L.

2. **Cache is over- AND under-invalidating.** `_package_mtime()` (:252-278) sweeps every
   .py mtime — any git operation forces a full rebuild in an editable install; comment
   edits to taxjson.toml too. No inspectability. **Fix:** content-hash manifest
   `work/.deps.json` ({input: sha256, config_digest-of-consumed-settings, code_version});
   stale = hash mismatch OR recorded input missing (retires the deletion-blindness class
   generically). `taxjson run --explain-cache`. Effort M.

3. **`to_base.csv` never refreshes on a stable install** — `stage_currency_rates` calls
   `needs_rebuild(rates_path)` with NO inputs (:406); on a non-editable install the rates
   file is generated once and returned forever. Trades newer than its coverage fall past
   the 5-day lookback (taxjson_convert_currency.py:112-127) to the 1.35 default —
   totals silently wrong, surfaced only as a DIAGNOSTICS banner line. **Fix:** rebuild
   when max(date in file) < today or newest input CSV is newer; fail (not warn) when
   conversion would use the default for a covered currency. Effort S. **Do this one first.**

4. **No config schema.** `type = "Taxable"` (or any typo) matches neither partition
   (:918-921) — the account silently vanishes from the run while stale reports keep
   looking healthy. Unknown keys ignored; bad `country` explodes mid-run at argparse
   depth. **Fix:** ~60-line `validate_config()`: per-table allowed keys + did-you-mean,
   enum checks, inputs-dir ↔ [accounts.*] cross-check. Effort S.

5. **Stderr has three fates** — persisted to .diag; discarded on success
   (every `capture_diag=False` stage: raw merge/gains, holdings, all 17 exports, ccd,
   leaps, radar, crosslint); or leaked live mid-run (`run_capture` captures stdout only,
   :240-241). The devnulled-warning bug class survives because the default is
   per-call-site judgment. Banner filter drops non-`ok:/warning:/note:/error:` lines.
   **Fix:** persist-by-default diag envelope; banner-exclusion flag replaces discard.
   Effort M.

6. **Crypto path is a second-class fork that breaks the documented Kraken workaround** —
   it uses legacy `taxjson-merge` (warn-and-skip missing inputs — the exact hazard
   `--require-inputs` closed; also used for sheltered_base :944) and **never receives
   `--map`**, so the ticker.map GLOBAL-rule workaround KNOWN_ISSUES prescribes for
   Kraken assets is unreachable via `taxjson run`. **Fix:** teach merge2 `--fill-crypto`;
   one path; retire taxjson_merge from orchestration. Effort M.

7. **The back half of every run is uncached** — wash pass, 6 sum-subprocesses, holdings
   export, **17 separate taxjson-export invocations each re-parsing every gains JSON**
   (:799-801), ccd/leaps/radar/crosslint/fees all run every time. **Fix:**
   needs_rebuild guards + `taxjson-export --matrix` single invocation. Effort S-M.

8. **Stage failure = raw CalledProcessError traceback**, first failing account aborts
   the loop. **Fix:** structured failure footer (account/stage/exit/diag path),
   continue remaining accounts, distinct exit codes. Effort S, ~40 lines.

9. **`--account` conflates rebuild scope with report scope** — combined reports skip and
   go stale even though other accounts' cached outputs sit in work/. **Fix:** always
   assemble combined stages from freshest cached outputs; `--account` only narrows
   what's rebuilt. Effort M.

10. **CLI grammar: three positional grammars + one `--json`.** period required
    (events/divs/trades/gains) vs optional (divs-sum/fees…) vs absent (list); period-vs-
    account sniffing (:1458-1462) is the root of the token-collision limitation; --json
    exists only on fees-sum. **Fix:** uniform `[PERIOD] [ACCOUNT]` + `--account` escape
    hatch + shared emit(rows, json_flag). Effort M.

11. **`taxjson doctor`** — read-only preflight: per-CSV detection (report ALL
    undetectable files instead of group_inputs aborting on the first, :372-381),
    crypto-flag consistency, currency coverage, rates coverage, config schema,
    stale .part litter. Effort M.

12. **Wire `taxjson-diff` into the umbrella CLI** + post-run gains-delta summary
    (snapshot each _gains.json before rebuild; print "margin: 2 gains modified — taxjson
    diff margin"). The holdings-diff precedent already exists (:663-688). Effort S.

13. **`--verbose` + `work/run.log`** JSONL per stage: {ts, account, stage, cmd,
    duration_ms, exit, stale_reason}. Effort S.

14. **Settings resolution duplicated and drifting** — country-aware tax_date default
    computed twice (:446-448 vs :740-742); stage_wash_pass passes country unnormalized
    while stage_account normalizes; base-currency re-read three ways. **Fix:**
    `ResolvedSettings` dataclass from load_config. Effort S.

---

## Report 3 — Brokerage parsers

1. **The normalized-transaction schema exists only as comments and an LLM prompt**
   (taxjson_generate_parser.py:50-79 + the untyped dataclass). taxjson_brokerage.py:204
   silently discards unknown keys (the `qty`→`quantity` shim at :199 is fossil evidence).
   Convention drift is current: SPLIT `symbol_new` = `''` (Questrade :277, RBC :234) vs
   `symbol` (IB :731) — the exact mismatch that defeated split dedup in round 2. Signs
   are per-action and documented only in scattered comments. **Fix:**
   `brokerages/schema.py`: per-action required/optional field table + invariants
   (action enum, date shapes, sign rules, SPLIT ratio>0 + normalized symbol_new,
   qty*price*mult ≈ net ± fee); run inside taxjson_brokerage.main() (warn default,
   `--strict`); generate the LLM prompt block and SCHEMA.md from the same table. Effort M.

2. **No parser conformance kit — and CONTRIBUTING's onboarding path is fictional twice**:
   it points at `brokerages/__init__.py` (empty, 0 bytes) and `tests/fixtures/`
   (does not exist; zero CSV fixture files anywhere — everything is inline strings).
   **Fix:** `ParserConformance` base test class (schema pass, golden file, invariants,
   re-parse determinism, zero-drop accounting) + `taxjson-anonymize-sample` so real
   statements can become shareable fixtures. Effort M. This is what makes AI-generated
   and community parsers safe to accept.

3. **Unknown-row policy is inconsistent** — Kraken/Webull/IB count and summarize skips;
   Questrade (:163-164), RBC (elif ladder, no else, :133-153), Coinbase (:54-82) drop
   silently. **Fix:** base-class `count_skip()`/`emit_skip_summary()`; conformance
   asserts rows_seen == emitted + counted; surface as `taxjson-brokerage --lint`.
   Effort S-M.

4. **Webull reads hardcoded column positions despite locating the header** (webull.py:
   33-37 finds the header, then row[3]/row[6]/row[7]; the col-9-else-8 proceeds heuristic
   :63-71 exists because Webull already shifted columns once). Next shuffle = plausible
   wrong numbers; the 0-tx safety net won't fire. **Fix:** build name→index map from the
   header it already found; positional fallback only when header lacks names. Effort S.

5. **BaseBrokerage is a helper bag, not a contract** — every parser hand-assembles dicts;
   `description` missing from Kraken trades/Questrade transfers/Coinbase buys means
   `--security-overrides` silently can't fire there (same gap already fixed 3× elsewhere).
   **Fix:** typed `tx_trade()/tx_dividend()/tx_split()/tx_tax()` constructors with
   required kwargs + invariants; LLM scaffold generates against them. Effort M.

6. **Detection is a hardcoded 4-broker if-chain; the Webull marker is a false-positive
   magnet** — accepts any file containing "Account Number" (detect:66-67), so a future
   Fidelity/Schwab CSV likely misroutes to Webull before its own detector could exist.
   Kraken/Coinbase detected by filename prefix only (run.py:328-330). **Fix:**
   registry-driven `sniff(first_rows, content) -> confidence` per parser class; max
   above threshold; specificity beats chain order. Effort M.

7. **The AI parser scaffold generates yesterday's conventions** — its prompt says
   "T+1 if no settlement column" (contradicting the repo's own era-aware T+2/T+1
   decision), omits SPLIT semantics/symbol_new/sign-preservation/skip accounting;
   samples only the first 30 lines (statement-style exports never show the model whole
   row classes); ships raw statement lines to a third-party API with no anonymization
   or warning. **Fix:** generate prompt from the schema table; stratified sampling per
   action-code class; anonymize by default; hand the draft to the conformance kit.
   Effort S-M.

8. **IbBrokerage.parse_file is an 820-line single method** with mon_map ×3, isin_map ×2,
   ticker+ISIN regex ×2, suffix-strip regex ×4 — the tier-4 strict-lookup fix had to be
   applied four times because sections are copy-variants. **Fix:** mechanical extraction
   into per-section methods + module constants + `_IbParseContext`. Effort M,
   behavior-neutral.

9. **New-broker onboarding touches 3-4 files that don't reference each other** (+ the
   CONTRIBUTING errors above; taxjson_extractors.py imports the CLI module for side
   effects with a noqa apology). **Fix:** `brokerages/__init__.py` as the single
   registry (IDS tuple + sniff on each class); fix CONTRIBUTING to the real checklist.
   Effort S.

10. **`clean_number` silently coerces garbage to 0.0** (base.py:201-217) — corrupt Price
    → price=0 and the fee back-compute guard zeroes the fee too: plausible row, wrong
    money. EU decimal-comma `1.234,56` parses "successfully" as 1.23456. **Fix:**
    numeric_failures counter in the skip summary; detect the EU pattern and raise.
    Effort S.

11. **No parser provenance in output** — nothing records which parser version produced a
    file; regeneration-after-fix discipline lives in humans' heads. **Fix:**
    `metadata.parser_version` int per module (needs_rebuild treats bumps as input
    changes = automatic re-parse instead of --force discipline); optional
    source_file/source_line per tx (id-stable: compute_id uses a fixed component list).
    Effort S.

12. **RBC classifier chain = order-dependent substring matching** ('Tax' in desc matches
    "Taxable"; precedence encoded purely by elif order; both prior substring bugs here
    were point-patches of this genotype). **Fix:** word-boundary regexes like
    `_DIV_DESC_RE` already is + conformance bucket assertions. Effort S.

13. **Split-fill disambiguation mutates user-visible description** (" [fill #2]",
    base.py:262-284) to perturb the content hash; Kraken/Coinbase carry real txids.
    **Fix:** optional `fill_seq` component in compute_id; description tag becomes
    display-only. Effort S, batch with schema work.

14. **ticker_map.py mixes tax identity, platform display formatting, and a third
    floating OCC re-implementation** (:82-103 vs base.encode_occ_strike); the real map
    machinery lives in the bin script so library consumers import a bin module.
    **Fix:** move load/apply into lib; point dotted-OCC at encode_occ_strike; move
    formatters export-side. Effort S.

---

## Report 4 — Corp-actions

Roadmap re-verification: multi-step chain collapse is DONE (iterative with cycle guard,
corp_actions.py:339-368) — roadmap stale on that point. Still open: return_of_capital,
name_change, tender_offer, rights, s.50(1), stock_dividend, liquidation, US rules,
cross-broker dedup, manifest migration.

1. **`country=usa` + RBC merger = shares vanish silently** (top severity).
   rbc_direct.py:108-119 unconditionally skips merger/CIL rows ("owned by corp-actions"),
   but taxjson_run.py:498 only runs the stage when `_country_has_corp_rules(country)` —
   false for usa (RULES_BY_COUNTRY has only 'canada', corp_actions.py:1322). Rows are
   consumed by nobody; old shares stay in inventory forever, new shares never appear;
   the stderr note claiming "taxjson run invokes corp-actions for you" is false in this
   configuration. **Fix:** run extraction for every country and hard-error when events
   exist but no rules do; minimum: loud warning when CORP_ACTION_BROKERS inputs exist
   and the stage is skipped. Effort S (guard) / M (proper fix = single ownership below).

2. **The elections manifest — the subsystem's only audit artifact — defaults into the
   gitignored, "rebuildable, safe to delete" work/ cache** (taxjson_run.py:494-496,
   :1059-1067). Elections + hand-typed FMV/ACB hints are the one thing in work/ that is
   NOT rebuildable; deleting work/ destroys them and a re-prompt may be answered
   differently, changing filed numbers. Code already prefers inputs/<account>/manifest.json
   when present (:490-492) — it just never creates it there. **Fix:** default the
   manifest into inputs/ (version-controlled); legacy-read + auto-migrate from work/.
   Effort S.

3. **Manifest unversioned; orphaned elections invisible.** No version field
   (corp_actions.py:795-849); load() drops unknown keys so future fields get stripped on
   next save(). Orphan detection is one-directional (events without elections only).
   RBC event_ids hash the *resolved* symbol whose resolution depends on which other rows
   are in the statement (:637-644, :741-759); `account` is hashed (:119-120) so renaming
   an account orphans every election. **Fix:** version+migration table; `elect --audit`
   with fuzzy matching; drop account from the hash. Effort M.

4. **Merger provenance is destroyed in the lowering to SELL/BUY** — gain records carry no
   marker that a disposition was a corporate action (core.py:782-795, :1263-1281);
   .sum renders a merger disposition as an ordinary sale; the CRA-review chain
   "SELL ⇄ election ⇄ broker rows" exists only by manual cross-reference. **Fix:** stamp
   emitted rows `source='corp_action'` + event_id; propagate to gain entries; footnote
   marker in .sum; list applied event_ids in emitted metadata. Effort M.

5. **Three owners for corp actions; tax treatment depends on which broker reported the
   event.** IB spinoffs are auto-translated inside ib_extractor (:744-778) as
   DIVIDEND+BUY with no election, while Questrade spinoffs get the full deemed-dividend
   vs s.86.1 election. An IB user cannot elect s.86.1 through the pipeline at all. The
   IB NOTE also still advises a hand-added TRANSFER that would double-count against
   corp-actions emission for Canada users. **Fix:** corp_actions.py becomes single owner
   of election-bearing events (is_rbc_merger_row shared-predicate pattern already
   establishes the mechanism). Effort M.

6. **US rules are mostly a relabel.** The four Canada emitters (taxable exchange,
   basis-carryover rename, distribution, allocated-basis spinoff) are mechanically the
   §1001 / §368(a) (holding-period tacking falls out free via the SPLIT model) / §301 /
   §355 primitives. One genuinely new emitter needed: cash-boot mergers (gain to lesser
   of gain or boot). **Fix:** extract generic emitters with label/statute params;
   USA_MERGER/USA_SPINOFF RuleSpecs. Effort M (+M for boot).

7. **Non-interactive answers path missing** — headless failure lists event_ids, but the
   only way to set an election is a TTY prompt; `taxjson elect` can view/reset/redo but
   never set. **Fix:** `elect --set <event_id>=<election> --hint fmv_per_share=12.5`.
   Elections should NOT move to taxjson.toml (per-event content-hash-keyed data). Effort S.

8. **Cross-broker duplicate events structurally invisible** (per-broker invocation,
   IB hashes ISINs vs RBC hashes resolved symbols → different ids; dedup can't collapse;
   two prompts, double disposition). **Fix:** candidate-duplicate detection (same date
   ±3d, same target, same ratio) → "consider electing ignore for one" warning. Effort M.

9. **Feature ranking (Canada+US retail):** (1) return_of_capital — annual for
   REIT/income-ETF holders, silent ACB error today; (2) name_change auto-resolve —
   emit SPLIT ratio=1 + symbol_new, alias machinery gives rename + wash identity free;
   (3) US rules; (4) tender offers; (5) worthless securities s.50(1) (SELL at $0 gated
   on election). Shared prerequisite: `RuleSpec.auto_default` — apply without prompting,
   still write the manifest record. ROC needs one engine guard: warn when ADJUST pushes
   pool ACB negative (s.40(3) deemed gain, unmodeled).

10. **RBC pairing residual fragility:** bidirectional-substring company matching
    ("GOLD CORP" matches "BARRICK GOLD", :712-714); lone-receipt fallback can mis-pair
    two mergers in one week. **Fix:** qty×ratio consistency check (<5%) before accepting
    a pair; demote lone fallback to warned guess. Effort S.

11. **Small cluster:** HINTS_BY_ELECTION positional 2-or-3 tuples → Hint dataclass;
    hardcoded "file with your 2025 return" in s.85.1(5) option text (:1153) — wrong
    every later year and saved into manifests; bare KeyError on unknown country (:1344);
    _bump_time clamps at 23:59:59 silently dropping the sort guarantee at day-end.

12. **`elect --redo` tells the user to --force unnecessarily** (mtime chain already
    invalidates); and when the user skips re-running, reports silently disagree with the
    manifest. Consider auto-invoking the downstream rebuild. Effort S.

---

## Report 5 — Reporting / web

1. **No report-model layer; the same stack re-implemented ~10×.** Comment-stripping JSON
   loader at 10 sites; 7 independent fixed-width table renderers; per-ticker×currency
   bucketing 4+ times (including run.py's third copy with its own _tx_fee); TaxTransaction
   hydration duplicated in radar + explain; three position walkers outside the engine
   (radar's full ACB replay with its own copy of the split-dedup key, safe_to_sell's
   FIFO walk, export's position-round walk whose comment promises engine equivalence
   "enforced only by hope"). This is why fixes kept landing tool-by-tool. **Fix:**
   `lib/report_model.py` — relaxed loader, typed GainEntry/IncomeEntry views,
   bucket_by(), one Table renderer (promote leaps_missed's), prepare_books() shared
   entry. Migrate tool-by-tool. Effort L overall, S per migration.

2. **The web layer parses fixed-width .rpt text back into data** (web/data.py:49-70:
   split on "|", hardcode parts[0..4], sections keyed on "--- " prefix, advisory prose
   is the schema, shape-check failures drop rows silently). **Fix:** radar emits a JSON
   sidecar (it already builds table_data as structured rows at :452); .rpt and web both
   render from it. Effort M.

3. **Staleness invisible; radar bakes run-relative countdowns into a static file** —
   "clears in 14d" is wrong 5 days after generation, for a deadline-discipline tool.
   No page shows generation time or inputs-changed-since. **Fix:** emit absolute
   clears_at dates and derive countdowns at view time; freshness banner in base.html.
   Effort S/M.

4. **leaps_gains and ccd_gains are the same file twice** (~95% identical; header/format
   strings byte-identical; the shared LONG/SHORT-inference heuristic must be fixed in
   two places). **Fix:** one `taxjson-option-gains --direction long|short [--right C|P]`
   + thin aliases. Effort S.

5. **what_if_sell is a hand-rolled partial pipeline replica** with bare
   `except Exception: pass` around phantom and ticker.map application (data.py:149-151,
   :168-170) — corrupt phantoms.json silently simulates on different books than the .sum;
   affiliated files omitted entirely; full history replayed per request with no caching.
   **Fix:** prepare_books() (same as Report 1 finding 2); thread a warnings list into
   the result; mtime-keyed book cache. Effort M.

6. **.sum is a concatenation of three subprocess stdouts** while cmd_summary imports
   summarize_gains directly to make numbers match — proof the dict is the real artifact.
   **Fix:** write `reports/<account>_report.json` once; render .sum, taxjson summary,
   web, and future exports from it. Also dissolves the fee triple-count pitfall's home.
   Effort M.

7. **Filing-day outputs don't exist; README oversells them.** README claims 8949/Sched D
   and Schedule 3 friendly summaries; nothing emits a fileable artifact; .tt is the
   home-grown interchange, not TurboTax. The gains JSON already carries everything per
   lot (cost, proceeds, disallowed_amount = code W, term, days_held, dates).
   **Fix (ranked):** (1) `taxjson-form-export --form 8949|schedule3`; (2) slip
   reconciliation `taxjson-reconcile-slips t5008.csv|1099b.csv` — turns the README's
   "always reconcile" homework into a report; (3) year-end self-contained HTML package.
   Effort M each.

8. **Project-specific data hardcoded in sold_performance** — two ticker→acquirer
   mappings and their merger exchange ratios (qty*ratio) applied to everyone's rows
   (:120-121, :143-144). Belongs in yf_ticker.map with an optional ratio column.
   Effort S.

9. **sum_income's get_base_ticker is an identity function** (splits on last dot and
   rejoins) while sum_gains uses the real get_underlying — the two halves of the same
   .sum bucket differently. Delete; import from ticker_map. Effort S.

10. **safe_to_sell is an unwired, weaker radar subset** — different inventory model
    (FIFO lots vs pool), skips TRANSFER/OPENING_BALANCE (:83), not invoked by any stage;
    every window/split fix must now land in three places. **Fix:** deprecate into
    `taxjson-wash-radar --locked-only`, delete after a release. Effort S.

11. **Inconsistent report-family CLI** — color flags differ per tool (explain's --no-color
    is a suppressed no-op), year scoping present/absent/differently spelled, --json only
    on fees, stdin support inconsistent; two ANSI blocks pasted twice within sum_gains
    itself. **Fix:** report_cli.base_parser() in the report-model lib. Effort M.

12. **Missing high-value web views** (data already exists): gains/income summary page;
    per-lot drill-down (render_gain_block); wash-chain timeline (the engine already emits
    fully structured wash_window data consumed only by the text renderer —
    trace_format.py:62-183 — a ready-made timeline dataset and the most differentiating
    view available); year-over-year; HTMX partial updates for what-if iteration.

13. **Report stages spawn subprocesses for same-package code**, flattening structured
    errors to stderr text. In-process calls once library entry points exist. Effort S.

Feature roadmap (ranked): 8949/Sched3 export → slip reconciliation → web gains summary +
staleness banner → wash-chain timeline → HTML tax package → per-symbol ACB history export
(`taxjson acb SYMBOL`) → radar .ics deadline alerts → year-over-year view.

---

## Report 6 — Product/domain + cross-cutting

### A. Tax-domain gaps (ranked)

1. **Wash/superficial-loss matching is blind to options as replacement property** —
   both engines match by symbol equivalence only (core.py:917-926 + US pre-pass);
   selling shares at a loss and buying a call inside the window is never flagged.
   CRA s.54 explicitly includes "a right to acquire"; IRS treats ITM calls as
   substantially identical. The `cross_asset` param (all three signatures) is the
   forgotten hook. No test exercises option↔share matching. **Fix:** parse OCC to
   (underlying, right, strike, expiry) (helpers exist in base.py); CALL acquisitions on
   the underlying become replacement candidates (CA: any right; US: flag-with-warning
   for OTM judgment calls); gate on cross_asset. Effort M.

2. **Return-of-capital is actively discarded** — the engine supports ADJUST
   (core.py:569-579; comments at :1374/:2416 even prescribe it) and .tt accepts ADJUST
   lines, but Questrade strips "RETURN OF CAPITAL ON" as description noise
   (questrade.py:32), IB ROC flows through as plain dividend income, RBC routes to
   _build_dividend. ACB overstated → gains understated at sale (CRA reassessment
   exposure) + ROC over-reported as income now. **Fix:** (a) document the "one ADJUST
   line per fund per year from your T3" workflow (S, most of the value);
   (b) parser classification of ROC-flagged rows (M). Ties into corp-actions ROC rule.

3. **DRIP rows book the income but not the shares** — RBC's if/elif dispatch means
   'ETF DISTRIBUTION REINVESTED' (pinned by test_rbc_parser.py:155) can only produce
   DIVIDEND records; no BUY leg → shares never enter the ACB pool → later oversell/
   phantom-short noise. (Confirm against a real DRIP statement whether RBC ships a
   separate purchase row.) The right pattern already exists in-repo: Coinbase staking's
   paired income + zero-net BUYSELL. Effort S.

4. **T1135 support — the most differentiating feature available.** Category 7 needs
   per-foreign-security max-cost-during-year, year-end cost, income, gain/loss — all
   already computed or trivially derivable from the merged taxable base + income +
   gains JSONs. Penalties run $25/day even when no tax is owed; most of the target
   audience crosses the $100k cost threshold. `taxjson t1135` = chronological cost walk
   + join income/gains + non-CA-domicile filter. Reporting-only, no engine risk.
   Effort M.

5. **§1256 contracts get regular FIFO instead of 60/40 MTM** (docstring-acknowledged
   out-of-scope) — but the tool gives NO warning when it processes SPX/XSP/NDX/RUT.
   Minimum viable: hardcoded broad-based-index list → 60/40 character tag in sum_gains +
   loud "open §1256 position at year end, MTM not computed" warning (S); full Dec-31
   MTM later (+M).

6. **Coinbase Send/spend rows fall through silently** (coinbase.py:65-66) — crypto
   spent/gifted is a taxable disposition; dropped with no counter (contrast Kraken's
   ignored-row summary). **Fix:** step 1 mirror the ignored-type summary (S);
   step 2 map spend-like types to SELL at FMV via fill-crypto (M).

7. **No multi-year loss carryforward/carryback ledger** — config is single-year; Canada
   3-back/indefinite-forward (T1A carryback is free money most filers miss), US $3k +
   carryover worksheet. Engine replays full history anyway. `taxjson carryover`:
   year-by-year net-gain table + running CA balance + US worksheet line + "you can carry
   $X back to 20YY". Reporting-only. Effort M.

8. **US engine FIFO-only** — no spec-ID/HIFO knob; can't reconcile against a 1099-B
   computed under an elected method (which the README tells users to do). Per-account
   `lot_method` + optional per-sale override file; wash interaction needs care.
   Effort M-L. (Same as engine finding 11.)

Checked and deliberately NOT gaps: 2024 two-tier inclusion rate (deferred then cancelled
March 2025; staying at 50% and applying it on Schedule 3 is correct — worth one README
sentence); LEAPS LT treatment (correct via held_more_than_one_year); §475/s.39(4)/§1092/
constructive sales/QCC (legitimately out of scope, transparently documented);
qualified-dividend split (1099-DIV delivers it); staking timing (already correct).

### B. Cross-cutting engineering (ranked)

1. **No property/invariant test layer** — the suite is uniformly example-based; zero
   fixture files on disk; the recurring bug classes (sign flips, split double-apply,
   date-basis mixing, unit mismatches) are exactly what invariants catch mechanically:
   share conservation, basis conservation, same-timestamp permutation invariance,
   year-partition sum == unfiltered total, parser round-trip identity. The one
   investment that ends the audit treadmill. Effort M.

2. **Schemas undocumented and unversioned** — no SCHEMA.md, no schema_version in
   *_base.json/*_gains.json/holdings.toml; README reserves the right to break names
   pre-1.0 while downstream consumers already exist. Fix: SCHEMA.md generated from the
   dataclass + "schema_version": 1 + a compat test. Effort S; prerequisite for releases.

3. **CI runs tests only** — no ruff/mypy/coverage anywhere; extras (web/fx/analytics)
   never installed in CI so those suites skip silently and can rot. The dead cross_asset
   param surviving five audits is the demonstration. Add ruff + opt-in mypy + one
   `.[all]` matrix cell. Effort S.

4. **Not on PyPI, no releases, no CHANGELOG, static 0.1.0** — for a tax tool,
   "I filed 2025 with taxjson X.Y.Z" reproducibility matters more than usual. Tag
   releases, CHANGELOG (the audit-fix history is already written in the memory files),
   trusted-publisher workflow. Effort S.

5. **Runbook gaps:** new-year rollover, amending a prior year, adding a broker mid-year —
   the three lifecycle events every user hits, none documented (~a page each). Plus the
   CONTRIBUTING fixtures/registry drift (Report 3). Effort S.

6. **CA wash solver is O(losses × all_txs) per iteration** (`for t in all_txs:`
   core.py:924, :1077; US pre-pass similar) inside a converge loop over full multi-broker
   history — the one real scaling cliff. Fix: pre-index per alias-symbol sorted lists +
   bisect the ±30d window. Also run.py re-parses taxjson.toml at 6+ call sites. Effort S.

7. **Test-runner ergonomics:** flat serial unittest, no fast/slow split, no coverage ever
   measured (KNOWN_ISSUES' coverage-gaps section is maintained by hand). Adopt pytest as
   runner-only, mark e2e slow, -n auto, coverage in CI. Effort S.

Positives worth keeping: dependency-free core with well-partitioned extras, atomic
.part-rename writers, deterministic content-hash IDs with documented rationale,
fail-loud parser philosophy, SECURITY.md + templates, example corpus with expected-output
checks.
