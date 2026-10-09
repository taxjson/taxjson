# Architecture map

Where the code for each part of taxjson lives, so a diagnosis or a change can
go straight to the right file. taxjson is a Python package: a Canadian
capital-gains toolkit (ACB, superficial loss, income, filing forms) built from
broker CSV exports, with an experimental US engine. The command is `taxjson`,
or `tjs` for short.

Several files are very large. `src/taxjson/bin/taxjson_run.py` (about 22k
lines) holds the whole CLI: every `cmd_*` handler, the `stage_*` functions of
`taxjson run`, and the argument parser. `src/taxjson/lib/core.py` (about 7.6k)
holds both gains engines. `src/taxjson/lib/brokerages/ib_extractor.py` (about
5k), `src/taxjson/lib/corp_actions.py` (about 4.4k), `src/taxjson/lib/tax_logic.py`,
`src/taxjson/lib/brokerages/rbc_direct.py`, `src/taxjson/lib/missing_history.py`
and `src/taxjson/lib/checklist.py` are 2k to 3k lines each. Do not read them
top to bottom: search them for the function name listed below ("def name("),
then read around the hit.

Conventions used here:

- `src/taxjson/bin/taxjson_run.py` — `cmd_run`, `stage_account`: a command `foo-bar` is handled by its `cmd_<name>` function in this file (dashes as underscores), with a few older names (`events` is `cmd_transactions`, `trades` is `cmd_buysell`, `list` is `cmd_positions`, `sum` is `cmd_summary`); `_build_parser` maps each name with `set_defaults(func=...)`.
- Each stand-alone tool `taxjson-<tool>` is `src/taxjson/bin/taxjson_<tool>.py` with a `main()`; `taxjson run` calls them through `_cmd("taxjson-<tool>")`, usually in the same process.
- The logic is in `src/taxjson/lib/`; commands and tools are in `src/taxjson/bin/`. A view in `taxjson_run.py` usually reads the files `taxjson run` left in the project's `work/` and `reports/` folders.
- Rule ids such as CA-SL-02 or US-WASH-22 are the statements `taxjson tax-logic` prints (`src/taxjson/lib/tax_logic.py`); code comments and tests cite them. Search the id to find the code that implements a rule.
- Tests live in `tests/`, mostly one file per fix round or feature; search a function name there to find what pins it.
- The playbook of known problems is `docs/troubleshooting.md`; the house style for everything printed is `docs/output-style.md`.
- On each bullet, the names after the path are literal strings in that file: functions, classes, constants, or a distinctive message or shell word.

## The CLI and command dispatch

`main` builds one argparse parser with a sub-parser per command and runs the
chosen handler. The help page groups commands under `_COMMAND_GROUPS`
headings. Before a handler runs, `_main` applies the project guards: `-C DIR`,
unknown or path-shaped account names, and the country check (a command the
other country owns is refused). Several commands on one line (`taxjson run sum`)
are split into segments and run in order.

- `src/taxjson/bin/taxjson_run.py` — `main`, `_main`, `_build_parser`, `_COMMAND_GROUPS`, `_GroupedHelpParser`, `_enforce_command_country`, `_refuse_unknown_account`, `_split_command_segments`, `_help_country`, `_failure_headline`, `_die`: the entry point, the parser and the grouped help page (the other country's commands hidden); the checks applied before every command; command chaining (`taxjson run sum`); how a failed stage or bad input becomes one short message.
- `src/taxjson/lib/cli_diag.py` — `guard_main`, `run_top_level`, `labelled_usage_errors`, `describe_input_error`, `tolerant_stdout`: the top-level wrapper every tool runs under (broken pipes, unreadable input, usage errors, the settling streams that print a multi-line message's blank line).
- `src/taxjson/lib/country.py` — `COMMAND_COUNTRY`, `command_country_problem`, `flag_country_problems`, `given_flags`: which commands and flags belong to one country.

## Stand-alone tools and in-process dispatch

Each pipeline stage is also a console script (`[project.scripts]` in
pyproject.toml). The scripts go through a trampoline that sets an owner-only
umask. `taxjson run` builds the same command lines, but `run_cmd` runs a
known tool in this process unless a TTY is needed or `TAXJSON_DISPATCH=subprocess`.

- `pyproject.toml` — `project.scripts`, `taxjson-merge2`, `taxjson-gains`, `taxjson-corp-actions`: the list of console scripts and the module each one runs.
- `src/taxjson/bin/_entry.py` — `private_umask`, `__getattr__`: the trampoline from `taxjson-<tool>` to the module's `main()`; `src/taxjson/bin/__init__.py` — `_os.umask` sets the same umask for `python -m taxjson.bin.<tool>` (while `sys.argv[0]` is `-m`).
- `src/taxjson/lib/dispatch.py` — `run_cmd`, `tool_module`, `_use_subprocess`, `TAXJSON_DISPATCH`, `python_module_argv`: in-process execution of a tool, with the subprocess fallback; every child Python process is launched through `python_module_argv` (`-P`, or the `_SAFE_BOOT` bootstrap before 3.11) so the current directory is never on `sys.path`.
- `src/taxjson/bin/taxjson_run.py` — `_cmd`, `run_to_file`, `run_capture`, `_exec_tool`, `_run_cmd`: how the orchestrator calls a tool and captures its stdout and `.diag` stderr.

## The taxjson run orchestrator

`cmd_run` reads and checks taxjson.toml, takes the project lock, builds the FX
rate file, then runs every account's stages. It parses all accounts first
(crypto sends, own-account moves, cross-listings and Questrade codes need
every account's evidence), then builds each account's books and gains, then
the cross-account wash passes, the cross-account reports, the filed-year
drift check and the end-of-run summary. A full rebuild is the default;
`--fast` reuses cached stages only when this same code built them.

- `src/taxjson/bin/taxjson_run.py` — `cmd_run`, `_acquire_run_lock` (held through `safe_write.file_lock`), `_refuse_outside_dir_links` (a written folder linked outside the project stops every command, from `load_config`), `_loose_project_dirs` (the once-per-run warning about a folder other users can read), `needs_rebuild`, `_package_fingerprint`, `_package_files`, `stage_account`, `stage_wash_pass`, `stage_blended_wash_pass`, `stage_cross_reports`, `stage_fees`, `collect_diagnostics`, `echo_attention_lines`, `_first_run_summary`: the run, its one-run-per-project lock and the `--fast` cache rules; `stage_account` is the per-account chain (parse, corp actions, .tt files, transfer arrivals, merge, gains, raw holdings, the `.sum` report); then the passes across accounts; what each stage printed, folded into the console and the `.sum` DIAGNOSTICS section; the closing summary.
- `src/taxjson/lib/first_run.py` — `collect`, `render`, `render_blocks`, `uncovered_short_sales`, `engine_booking`, `zero_cost_positions`, `income_without_position`, `SUMMARY_FILE`: the "what to check next" counts at the end of a run; `engine_booking` reads the gains files so a sale is called "NOT in `taxjson sum`" only when they lack it (the run's mid-run note, before the wash pass, reads the plain ones: `prefer_wash=False`).

## Input discovery and broker detection

Each account's folder under `inputs/` is scanned; every CSV is routed to a
parser by its content (header signature), then by its name prefix, never by
guessing. A file that matches two parsers or none stops the run with a
message naming the file.

- `src/taxjson/bin/taxjson_run.py` — `input_files`, `group_inputs_detailed`, `detect_broker`, `_report_detection`, `spreadsheet_inputs`, `_duplicate_input_files`, `_sweep_retired_exports`, `_warn_shared_broker_accounts`: the folder scan, the one line per file saying how it was read, and checks on duplicate, retired or shared exports.
- `src/taxjson/lib/brokerages/detect.py` — `detect`, `detect_broker`, `DETECTORS`, `Detection`, `AmbiguousBroker`, `content_matches`, `name_hint`, `looks_like_ib_text`, `same_broker_siblings`: content-first detection (each parser's header signature is in `DETECTORS`), then the file-name hint; IB statement sniffing and sibling files.
- `src/taxjson/bin/taxjson_detect_brokerage.py` — `detect_brokerage`, `cannot_detect_message`, `main`: the `taxjson-detect-brokerage` tool, the same decision for one file.
- `src/taxjson/bin/xlsx_to_csv.py` — `convert_xlsx_to_csv`, `read_sheet`, `_clean_numeric_commas`: turns an .xlsx export into CSV before parsing.

## The parse stage

`taxjson-brokerage` loads one registered parser, runs it over the account's
files and writes normalized transaction JSON. Every row is checked against
the schema. ticker.map EXTRACT lines (security overrides) are applied here,
and broker account numbers are hashed before they reach the output.

- `src/taxjson/bin/taxjson_brokerage.py` — `main`, `register_brokerage`, `load_security_overrides`, `apply_security_override`, `stamp_source_accounts`, `hash_broker_account`, `final_record_cut`, `_dedup_evidence`, `_only_nonevents`, `_refusal`: the parse tool and the parser registry calls; trimming, evidence dedup and refusing an export that yields nothing usable.
- `src/taxjson/lib/core.py` — `register_brokerage`, `load_brokerage`, `_BROKERAGES`: the id-to-class registry the parse tool fills.
- `src/taxjson/bin/taxjson_extractors.py` — `main`: lists the registered parser ids and classes.
- `src/taxjson/bin/taxjson_run.py` — `_parse_reads`, `_parse_outputs`, `_reused_parse`, `_split_generic_groups`: how `stage_account` calls the parse per broker group and reuses a cached parse.

## Broker parsers: shared base and schema

Every parser subclasses `BaseBrokerage` and implements `parse_file`. The base
class holds the shared helpers: OCC option symbols, currency suffixes, strict
number parsing, settlement dates and skip accounting. The schema module is the
one table of what a normalized row must look like.

- `src/taxjson/lib/brokerages/base.py` — `BaseBrokerage`, `parse_file`, `BrokerageParseError`, `count_skip`, `emit_skip_summary`, `format_occ_symbol`, `apply_currency_suffix`, `canonical_ca_listing`, `parse_strict_number`, `read_broker_text`, `equity_settlement_date`, `income_facts_from_description`, `set_ticker_map`, `ticker_map_mentioned`, `set_declared_renames`, `declared_rename_where`, `source_identity`: the base class and its row-accounting contract; option and listing symbols; strict number and text reading; settlement dates and income facts; what a parser knows of ticker.map and of the .tt RENAME lines, and of which account a file belongs to.
- `src/taxjson/lib/brokerages/schema.py` — `SCHEMA`, `KNOWN_ACTIONS`, `validate_transactions`, `render_schema_prompt`: the normalized-row schema and its validator.

## Equity broker parsers

One parser per broker export. Each reads the broker's activity CSV and emits
BUY, SELL, DIVIDEND, TAX, ROC, SPLIT, TRANSFER and the other actions in the
schema. Account-level context (positions over time, reversals, ticker
changes) is built across all of an account's files before rows are emitted.

- `src/taxjson/lib/brokerages/questrade.py` — `QuestradeBrokerage`, `QtAccountContext`, `build_qt_account_context`, `_plan_qt_reversals`, `_detect_qt_ticker_changes`, `scan_code_uses`, `_plan_qt_journals`, `_journal_listing`, `journal_codes`: Questrade activity exports, including internal security codes and BRW currency journals (paired legs, the US-dollar line, codes learned from a journal leg).
- `src/taxjson/lib/brokerages/rbc_direct.py` — `RbcBrokerage`, `read_rbc_rows`, `classify_rbc_row`, `RbcAccountContext`, `build_rbc_account_context`, `_plan_reinvest_reversals`, `rbc_coverage_messages`, `is_holdings_export`, `RbcFormatError`: RBC Direct Investing; every row is classified by its activity label and event code; date coverage, holdings files and format refusals.
- `src/taxjson/lib/brokerages/ib_extractor.py` — `IbBrokerage`, `parse_file`, `prepare_files`, `reconcile_files`, `resolve_unmatched_ca`, `ib_year_coverage`, `get_ib_settlement`, `_ib_market_trade_date`, `_ib_xfer_cancels`, `_ib_fold_refund`, `_ib_temp_folds`, `_map_names_temp`, `_project_map_names`, `_ib_fold_rows`, `_warn_stock_aliases`, `_security_name`: Interactive Brokers activity statements (every section of one CSV; a row's security name from the instrument on its own listing's market when several share its symbol); coverage gaps, settlement and trade dates, cancellations and fee refunds; IB's temporary time-stamped symbols folded onto their ticker (unless a ticker.map line names the stamped symbol), and one contract under two symbols (a dated RENAME hint).
- `src/taxjson/lib/brokerages/webull.py` — `WebullBrokerage`, `label_hits`, `_WEBULL_OPTION_RE`, `_deliverable_size`: Webull exports (both column layouts, matched by header label).
- `src/taxjson/lib/trade_cancel.py` — `pair_cancellations`, `is_trade_cancel`, `TRADE_CANCEL_TYPE`, `trade_cancel_what`: an IB `Ca` cancellation netted against its original (a partial one reduces it; later ones match what is left; a cancellation of the whole order after one of its executions' removes it in full: `overlaps`).
- `src/taxjson/lib/futures.py` — `settle_futures`, `is_plain_future`, `method_for`, `section_1256_kind`: plain futures booked on a settlement basis.

## Crypto parsers

Kraken and Coinbase exports are read strictly: amounts must parse exactly, and
local times need a time zone. Withdrawals and deposits are kept as TRANSFER
custody evidence for the crypto sends step.

- `src/taxjson/lib/brokerages/kraken.py` — `KrakenBrokerage`, `header_kind`, `REQUIRED_BY_KIND`, `known_assets_of`, `_ledger_identity`: Kraken trades and ledger exports.
- `src/taxjson/lib/brokerages/coinbase.py` — `CoinbaseBrokerage`, `resolve_header`, `is_header_row`, `_cb_symbol`, `_HEADER_SYNONYMS`: Coinbase transaction exports.
- `src/taxjson/lib/brokerages/_crypto_common.py` — `strict_money`, `utc_to_local`, `LocalTimezoneMissing`, `usd_value`, `warn_depeg`: helpers both crypto parsers share.
- `src/taxjson/bin/fill_crypto_prices.py` — `main`, `get_crypto_price`, `load_cache`, `yahoo_id`, `_implausible_yahoo_prices`: the `taxjson-fill-crypto` stage that prices crypto rows with no fiat value.

## Generic CSV, hand entry (.tt) and adding a parser

A broker with no parser can be read through a column mapping (`generic_*.csv`
plus its mapping). Hand-entered rows go in `.tt` text files (one transaction
per line), which `taxjson run` converts to JSON. A new parser registers in
`taxjson_brokerage.py`, adds a detector, and passes the conformance harness
against a synthetic fixture.

- `src/taxjson/lib/brokerages/generic.py` — `GenericBrokerage`, `mapping_path`, `_load_mapping`, `_check_keys`, `_check_trade_row`: the column-mapped importer.
- `src/taxjson/bin/taxjson_convert_tt.py` — `tt_to_json`, `parse_tt_line`, `parse_opening_line`, `json_to_tt_lines`, `compute_tt_id`, `main`: the .tt format in both directions.
- `src/taxjson/bin/taxjson_run.py` — `stage_account`, `_refuse_crypto_openings`: where an account's `.tt` files are converted; an OPENING line in a crypto account is refused.
- `src/taxjson/lib/tt_totals.py` — `read_diag`, `project_mismatches`, `Mismatch`, `tolerance`, `source_line` (an ACQUIRED line quoted as written): a `.tt` line whose total is not qty x price +/- fee (booked as written), read back from the stage's .diag; `src/taxjson/bin/taxjson_run.py` — `_echo_tt_totals` shows each on the console on every run and `--strict` stops on it; `src/taxjson/lib/checklist.py` — `d_run_clean` lists them.
- `src/taxjson/bin/taxjson_generate_parser.py` — `main`, `identity_findings`, `_call_claude`, `_call_gemini`: maintainer tool that drafts a parser from a sample CSV.
- `tests/parser_conformance.py` — `ParserConformance`, `FIXTURES`, `validate_transactions`: the harness every parser test subclasses (schema, golden output, determinism, row accounting).
- `tests/test_parser_conformance.py` — `TestQuestradeConformance`, `TestIbConformance`, `TestKrakenConformance`, `UPDATE_GOLDEN`: one registration per parser.
- `tests/fixtures/ib/expected.json` — `action`, `date_settle`: an example golden; each broker folder under tests/fixtures has a synthetic `sample.csv` and its `expected.json`.

## Merge, sort, dedup and validate

An account's parsed files are merged, sorted and deduplicated across files,
ticker.map is applied, amounts are converted to the base currency, fund
distributions are added, and the result is validated: the account's
`work/<name>_base.json`. `taxjson-merge2` does these steps in one call.

- `src/taxjson/bin/taxjson_merge2.py` — `main`, `cancel_trade_pairs`, `reconcile_dividend_tax`, `canonicalize_split_ratios`, `warn_duplicate_splits`: the combined merge tool.
- `src/taxjson/bin/taxjson_merge.py` — `main`: concatenates transaction files.
- `src/taxjson/bin/taxjson_sort.py` — `plan_dedup`, `DedupPlan`, `deduplicate`, `sort_transactions`, `_tt_near_duplicates`: sorting and cross-file dedup; ambiguous duplicates are reported, not dropped.
- `src/taxjson/bin/taxjson_validate.py` — `validate_transactions`, `main`: the final shape check on a book.
- `src/taxjson/lib/json_input.py` — `read_json_doc`, `load_json_doc_or_exit`, `check_row_types`, `require_gains_doc`, `check_gains_rows`, `gains_row_kind`, `read_work_doc`, `rows_or_exit`, `filing_json_text`, `dump_filing_json`: how every tool reads a JSON input (a missing list, a wrong type or a NaN / Infinity is refused; every disposition of a gains file carries its units, gain and a date — the one row contract of the filing and summary readers), and how filing figures are written (a non-finite number refused).
- `src/taxjson/bin/taxjson_diff.py` — `main`, `extract_records`, `make_key`, `field_diffs`: compares two transaction or gains files (ADDED / REMOVED / MODIFIED).

## ticker.map, renames, cross-listings and symbol codes

ticker.map is one keyword-prefixed rules file at the project root holding
standing truths: spellings, consolidations of two listings (TOBASE),
deletions, EXTRACT fixes, T1135 countries. A ticker change and a journal
between two listings are dated events, `.tt` lines of an account written date
first (`RENAME <date> OLD NEW`, `JOURNAL <date> FROM TO <qty>`): the position
and its cost carry to the new ticker on that date; the journal's legs join the
two listings as one security. The legacy ticker.map JOURNAL (read as TOBASE)
and dated RENAME lines still work; `taxjson format-map --write` migrates
them. Two listings joined by a transfer journal become one security, and
Questrade internal codes are resolved to real tickers from the other accounts'
evidence.

- `src/taxjson/lib/ticker_map.py` — `find_ticker_map`, `read_side_rules`, `parse_side_line`, `SideRules`, `map_ticker`, `refuse_legacy_map_file`: reading ticker.map and mapping one symbol.
- `src/taxjson/bin/taxjson_ticker_map.py` — `apply_mapping`, `load_map_file`, `map_file_problems`, `merge_renames`, `bare_target_warnings`, `guard_option_listing_collisions`, `named_symbols`, `_parse_map_text`, `distinct_spellings`, `listing_spelling_notes`: the `taxjson-ticker-map` stage applied to a book; a bare US ticker beside a share listing also read as the US listing in a DISTINCT line (a rename-type line so written warned when the books hold the US listing: `src/taxjson/bin/taxjson_run.py` — `_say_listing_spellings`); the symbols a map's lines name (`lookups=True`: a lookup line's symbol too); a rename-type line joining an option or a future with shares, or two contracts, refused (`src/taxjson/bin/taxjson_convert_tt.py` — `_join_derivative_check`).
- `src/taxjson/lib/renames.py` — `DatedRename`, `rename_events`, `apply_dated_renames`, `after_rename_row`, `RenameNeedsCountry`, `late_rows`, `declared_late`, `unresolved_late`, `unused_declarations`, `row_source`, `row_source_id`, `SOURCE_LABELS`, `KIND_CRYPTO`, `rename_hints`, `report`, `render`: renames as dated events and `taxjson renames` (each rename's source; applied in date order, to the accounts of the declaring kind, `late=` per account — `DatedRename.late_for`, the event's own only for the accounts that held OLD; `late=fold` re-books the rows the country's engine takes after the account's own rename row — `after_rename_row` compares `event_sort_key` keys, `ca_main` or `us_main`; OLD includes the raw spellings an undated rename maps onto it; a declaration that books nothing listed; the look-alike renames the exports' .diag hints show, suggested with the `.tt` line that books each).
- `src/taxjson/lib/dated_events.py` — `read_declarations`, `check_against_map`, `resolve_renames`, `project_renames`, `settle_journals`, `write_sidecars`, `journal_legs`, `effective_lines`, `apply_meta`, `tt_renames`, `rename_records`, `state_doc`, `read_state`, `legacy_note`, `home_accounts`, `plan_migration`, `account_kind`, `Journal`, `STATE`: the `.tt` JOURNAL and RENAME lines of every account (checked up front), a journal settled against the broker's own legs and booked as transfer evidence (work/<acct>_tt-journal_transfers.json), every declaration of a ticker change merged into one event (contradictions refused) and written into the run's effective map, `format-map`'s simulation of the run before and after a migration, and the record of every dated event (work/dated_events.state, each with its source and place).
- `src/taxjson/bin/taxjson_convert_tt.py` — `parse_journal_line`, `parse_rename_line`, `_EVENT_ACTIONS`: the two dated-event `.tt` lines (date first, never rows of the converted file).
- `src/taxjson/lib/journals.py` — `report`, `render`, `source_of`, `SOURCES`, `_Map`, `_broker_journals`, `_classify`, `FORMAT`: `taxjson journals` — every broker journal between two listings, per account, with how it was found and its state (joined with the map line that pools it, suggested with the lines that settle it, refused with the undo), read from work/cross_listings.state, the parsed exports and the effective map.
- `src/taxjson/lib/cross_listings.py` — `gather`, `analyze`, `shown_apart` (the exports' evidence that two same-root listings are NOT one security: a receipt, different companies), `map_lines`, `effective_map_text`, `joined_note`, `companies_differ`, `collisions`, `extract_words`, `collision_note`, `journal_wording`, `_explicit_journal`, `_journal_names_verdict`, `_trailing_form`, `_hub_partners_agree`, `business_days`, `read_state`, `STATE_KEYS`: two listings joined by their transfer journal (the pairs left alone — a DISTINCT line, the user's map, two companies — recorded as `refused` for `taxjson journals`) (a broker's explicit journal pair by its legs' own names; the window in business days); a symbol naming two companies (a fund's US-dollar unit beside an NYSE stock) and the EXTRACT line that separates them; a Questrade currency journal the parser paired, joined as a TOBASE line (Canada only); a `.tt` JOURNAL line's pair joined when one root or agreeing names show one security (`declared`, `_claim`, `declared_verdict`, `listing_root` — the venue suffix only —, `receipt_why` — a depositary receipt is its own root —, `declared_twins`, `partial_overlaps`, `near_restatements`; both countries; else refused `UNPROVEN` and the run stops), its record carrying `source`; a pair the user's map decides read through the map's renames (`analyze` `map_renames`: an out-leg the map books as the in-leg's other listing joins the in-leg to it; legs the map books as two symbols are a Warning, `map_split`, `map_split_note`); a broker-referenced journal's legs pair inside their account before any cross-account cancelling (`analyze` step 0b; a reference group is one journal by `src/taxjson/lib/missing_history.py` — `ref_group_journal`, the rule `own_journal_legs` and `_detected_journals` share).
- `src/taxjson/lib/symbol_codes.py` — `resolve`, `project_evidence`, `names_agree`, `is_code`, `read_state`, `codes_note`, `exact_name`, `questrade_name`, `questrade_own_name`, `code_designators`, `listing_designators`, `designators_agree`, `rbc_name`, `_CONFIRM_RE`, `_cut_account_ref`: Questrade internal codes resolved to tickers (the account's own rows first, `how` "account"; a code's warrant / right / unit / preferred / class designators read over all its descriptions must agree with the listing's); the security names (dealer confirmation wording and a transfer's account reference cut) that codes and cross-listings compare. `src/taxjson/lib/corp_actions.py` — `parse_questrade_corporate_actions` (`symbol_codes`) and `src/taxjson/bin/taxjson_corp_actions.py` — `extract_events` (`--symbol-codes`) book a spinoff chain under an internal code as the record resolves it (the event id keeps the code).
- `src/taxjson/lib/listing_suffix.py` — `scan_questrade`, `scan_rbc`, `project_evidence`, `resolve`, `fixes`, `corrections_note`, `suggestions`: a Questrade / RBC bare ticker's listing read from the books, not the row currency, a transfer's out-leg read through the map's renames (applied by `taxjson-brokerage --listing-fixes`; another account's proof for the same broker and name counts, `Evidence.proved`); a Questrade dividend reinvestment binds to the account's held listing (`src/taxjson/lib/brokerages/questrade.py` — `_rei_listing`).
- `src/taxjson/bin/taxjson_run.py` — `stage_cross_listings`, `stage_dated_events`, `_read_dated_events`, `_write_dated_events_state`, `stage_symbol_codes`, `stage_listing_suffix`, `_listing_suffix_stale`, `_ticker_map_renames`, `cmd_ticker_map`, `cmd_renames`, `cmd_journals`, `_check_renamed_late`: where the run and the commands use them.
- `src/taxjson/lib/brokerages/ib_extractor.py` — `_warn_stock_aliases`, `_book_conid_renames`, `_project_tt_renames`, `_project_map_dated`, `_project_distinct`, `_conid_seen`, `_merge_seen`, `_conid_verdict`, `_conid_link`, `_declared_clash`, `_declared_join`, `_check_declared_dates`, `_account_tt_links`, `_ib_account_prescans`, `_ib_description_tickers`, `_ib_placeholder_ticker`: IB's one contract id under two symbols booked as a rename event (event_source `ib-conid`, at the new symbol's earliest row of any section) when that contract id's own rows date it (`_ib_prescan` `conid_rows`: position rows and every dated row a Description names, OLD's last row only from rows that move a position or its cost, `_CONID_MOVES`; `ca_links`), read from every IB account of the project (`prepare_files` `project`, from `taxjson-brokerage --project-statements`, which `src/taxjson/bin/taxjson_run.py` — `stage_ib_project` writes — always, `"accounts": {}` when no other account has an IB statement, so it stays a dep of the parse), else an ATTENTION line with the `.tt` line; never on top of a corporate action the parse books as the change (a pending entry `_book_conid_renames` settles from the parsed SPLIT rows); a declared date after an account's first new-symbol trade is refused, a declaration the other way round is an ATTENTION line.
- `src/taxjson/lib/ticker_map_format.py` — `format_map`, `init_template`, `GROUPS`, `meaning`, `commented_rule`, `FormatError`, `Moved`, `_migrate_items`, `_scan_template`, `header_text`: `taxjson format-map` (ticker.map in keyword groups, comments moved with their line, the parsed map kept identical; the legacy dated events migrated: JOURNAL to TOBASE, a dated RENAME moved out as a `.tt` line; an earlier taxjson header replaced) and the ticker.map `taxjson init` writes; `src/taxjson/lib/ticker_map_legacy.py` — `LINE_HASHES`, `PARAGRAPH_HASHES`, `FINGERPRINTS`, `near_legacy`, `corpus_tables`: every header earlier taxjson versions wrote, by hash (the texts: `tests/fixtures/ticker_map/legacy_headers.txt`); `src/taxjson/bin/taxjson_run.py` — `cmd_format_map`, `_migration_notes`, `_format_map_header_notes`.
- `src/taxjson/lib/xlist_loss_radar.py` — `analyze`, `Finding`, `open_findings`, `read_state`, `message`, `suggestion_reason`, `STATE`, `_roots` (a class share's root also without its letter), `_held_at` (splits scale), `RADAR_SHOWN`, `more_message` (the console's cap), `_scoped_names`, `_source_brokers` (the names of the loss's and the purchase's own rows), `_wording_only` (names differing only in the voting-share phrase one ends with, `_voting_phrase`: a "possible" pair): a loss on one listing with another listing of the same root (an equal name) bought within 30 days — a possible superficial loss / wash sale the books cannot see until ticker.map says TOBASE or DISTINCT (work/xlist_loss_radar.state); `src/taxjson/bin/taxjson_run.py` — `_say_xlist_losses` (the run's Warning, `--strict`); `src/taxjson/lib/ticker_map_suggest.py` — `from_xlist_loss_radar` (its `TOBASE` suggestion in `taxjson ticker-map --suggest`).
- `src/taxjson/lib/ticker_map_suggest.py` — `gather`, `pending`, `Suggestion`, `from_diag`, `from_cross_listings`, `from_xlist_loss_radar`, `from_symbol_codes`, `from_listing_suffix`, `clean_extract`, `books_symbols`, `covered_by_suggestion`, `appended_text`, `verify` (the MAP-GAP pairs, certainty "verify": asked TOBASE / DISTINCT on a terminal, never added by --all): `taxjson ticker-map --suggest` and `--write` (a template line is listed, never written; a conditional hint only when the books hold every symbol it joins).
- `src/taxjson/lib/t1135_country.py` — `parse_country`, `override_value`, `NOT_FOREIGN`: the country word of a T1135 line in ticker.map.

## Corporate actions and elections

Mergers, spin-offs, tenders and reorganizations are read from each broker's
corporate-action rows into `CorporateAction` events. Each event needs an
election (taxable or a rollover, by country); the choice is saved in a
manifest and turned into transaction rows. Undecided events stop the run with
an election list (a sheltered account's spin-off or merger is booked without
asking unless `sheltered_elections = "ask"`); `taxjson elect` records the
choices. Splits and renames use
one shared timeline so their arithmetic is the same everywhere.

- `src/taxjson/lib/corp_actions.py` — `CorporateAction`, `parse_ib_corporate_actions`, `parse_questrade_corporate_actions`, `parse_rbc_corporate_actions`, `RULES_BY_COUNTRY`, `RuleSpec`, `resolve_event`, `options_for`, `apply_auto_defaults`, `IGNORE_ELECTION`, `Manifest`, `ElectionRecord`, `combine_broker_copies`: the broker extractors; the election rules per country; the saved elections (the manifest) and dedup of one event seen by two brokers.
- `src/taxjson/bin/taxjson_corp_actions.py` — `main`, `EXTRACTORS`, `extract_events`, `_prompt_election`, `_pending_doc`, `_emit_resolved`, `EXIT_ELECTIONS_REQUIRED`: the `taxjson-corp-actions` stage (`--sheltered-elections`: a sheltered account's events booked without asking).
- `src/taxjson/lib/corp_actions.py` — `SHELTERED_DEFAULT`, `sheltered_elections_mode`, `sheltered_default_applies`, `sheltered_default_rows`, `sheltered_default_text`: a sheltered account's spin-off ($0 cost) or merger (cost carried) booked without asking, `[settings] sheltered_elections`.
- `src/taxjson/bin/taxjson_run.py` — `cmd_elect`, `_print_pending`, `_print_elections`, `_warn_zero_value_spinoffs`, `_note_sheltered_defaults`, `_sheltered_elections`, `_country_has_corp_rules`: `taxjson elect` and the run's election messages.
- `src/taxjson/lib/corporate_timeline.py` — `SplitTimeline`, `cumulative_factor`, `split_event_key`, `split_seen`, `event_sort_key`, `radar_priority`: split and rename arithmetic and same-day event order.
- `src/taxjson/lib/corp_views.py` — `spinoffs`, `splits`, `render_spinoffs`, `render_splits`, `wrong_country_elections`: `taxjson spinoffs` and `taxjson splits`.

## Currency conversion and FX rates

Every amount is converted to the home currency (CAD or USD) at the day's
rate. `stage_currency_rates` builds the project's rates file by calling
`taxjson-to-base-curr` per source currency (Bank of Canada, with fallbacks);
the rates are cached in the home folder.

- `src/taxjson/bin/taxjson_run.py` — `stage_currency_rates`, `_rates_coverage_stale`, `_raw_align_adjust_currency`, `_home_currency`: the run's rate file, its freshness, and ADJUST rows (and a purchase whose `listing_currency` is not its cash's, a Questrade REI) restated in the pool's currency for the raw holdings books.
- `src/taxjson/bin/to_base_curr.py` — `build_rates`, `fetch_boc`, `fetch_boc_noon`, `fetch_yahoo`, `refresh_boc`, `resolve_rows`, `CACHE_FILE`: daily rates for one currency pair.
- `src/taxjson/bin/taxjson_convert_currency.py` — `main`, `convert_transaction`, `get_rate_for_date`, `load_exchange_rates`, `MissingRateError`, `abort_if_currency_uncovered`, `fallback_rows`, `emit_fallback_summary`, `rate_source_summary`, `default_rate_for`: converting a book; rows priced with a fallback rate, and the summary of rate sources.
- `src/taxjson/lib/json_cache.py` — `save_json_cache`: atomic, locked saves of the shared price and rate caches.
- `src/taxjson/lib/offline.py` — `offline_enabled`, `ENV_VAR`: `TAXJSON_OFFLINE`, the switch that forbids network egress.
- `src/taxjson/lib/core.py` — `convert_currency`: the engine-side currency helper.

## Fund distributions and income dating

Non-cash fund distributions (reinvested capital gains, return of capital) are
entered in taxjson.toml `[[distributions]]` and added to the books. Which
tax year an income row belongs to, and what counts as a payment in lieu, is
decided per country from neutral facts the parsers record.

- `src/taxjson/bin/taxjson_apply_distributions.py` — `apply_distributions`, `main`, `balance_on`, `resolve_live_symbol`, `_warn_roc_overlaps`: the `taxjson-apply-distributions` stage.
- `src/taxjson/lib/project_tables.py` — `distribution_rows`, `claimed_losses`, `table_problems`, `DIST_TABLE`, `CGD_TABLE`: the hand-entered year tables in taxjson.toml.
- `src/taxjson/lib/cg_dividends.py` — `parse_map`, `entries_from_config`, `allocate`, `CgDividendMapError`: T5 box 18 capital-gains dividends.
- `src/taxjson/lib/income_dating.py` — `rules_for`, `IncomeRules`, `parse_ric_entries`, `is_canadian_issuer`, `split_share_roots`: income-year and payment-in-lieu rules.
- `src/taxjson/lib/pipeline.py` — `apply_roc_record_dates`, `apply_trust_roc_record_dates`, `income_dating_flags`, `add_income_dating_args`: how the gains run applies them.

## Transfers, crypto sends and opening balances

TRANSFER rows are custody evidence, kept out of the books unless something
decides otherwise. Shares that arrive in a taxable account from outside the
books are booked at the broker's stated book value, or flagged. In a US
project a move between two of your own taxable accounts carries its lots. A
crypto send that never arrives in another of your accounts may be a
disposition, decided once and saved. Opening balances come from a broker's
positions report.

- `src/taxjson/lib/transfer_in.py` — `Arrival`, `arrivals`, `sidecar_rows`, `own_journal_legs`, `movable_rows`, `booked_rows`, `mark_covered`, `attention_lines`: shares that arrived by transfer; a journal inside one account settled within its own pair.
- `src/taxjson/lib/in_kind.py` — `Leg`, `Move`, `legs`, `pair_all`, `Pairing`, `pair`, `line_legs`, `in_parts`, `parts_message`, `apply_lines`, `decide`, `value`, `booked_rows`, `mark_sheltered`, `message`, `contribution_results`: in-kind contributions and withdrawals between a taxable and a registered account (CA-INKIND-* / US-INKIND-*).
- `src/taxjson/bin/taxjson_run.py` — `in_kind_state`, `stage_in_kind_context`, `_say_in_kind`, `in_kind_taxable_legs`, `_registered_transfer_rows`, `_inkind_lines`, `_in_kind_close`: the run's in-kind moves (work/in_kind.json), the plan's purchase marked in work/sheltered_base.json, the one warning.
- `src/taxjson/bin/taxjson_convert_tt.py` — `parse_inkind_line`: the `.tt` INKIND line (a value, never a row of the books).
- `src/taxjson/bin/taxjson_run.py` — `stage_transfer_arrivals`, `transfer_arrivals`, `stage_own_account_moves`, `own_account_custody_moves`, `_stage_crypto_sends`, `cmd_crypto_sends`, `cmd_opening`, `_opening_lines`: where the run books arrivals and own-account moves; the crypto sends hook and `taxjson crypto-sends`; `taxjson opening`, which writes OPENING lines from a positions report.
- `src/taxjson/lib/crypto_sends.py` — `load_transfer_rows`, `match_transfers`, `build_report`, `record_decision`, `prompt_undecided`, `render_tt`, `DECISIONS`: crypto sends, their decisions and the generated crypto_sends.tt.
- `src/taxjson/lib/opening.py` — `apply_opening_cutoff`, `snapshots`, `OpeningError`, `ATTENTION_OPENING`, `_realizations_left_out`: OPENING rows and the cutoff they impose on earlier rows (a left-out sale or short cover of the tax year stops the run).
- `src/taxjson/lib/pipeline.py` — `_handle_transfers`, `_net_cross_account_transfers`, `TransferValidationError`, `transfers_in_loss_windows`, `transfers_as_acquisitions`: transfer handling before the engine runs.

## Missing purchase history

When the files do not reach back to a purchase, a sale has no cost. These
sales are detected, listed with suggestions, and can be answered with a
`missing_history.json` entry (opening lots the gains run synthesizes) or a
draft of the missing purchases.

- `src/taxjson/lib/missing_history.py` — `detect_missing_history`, `MissingHistoryCandidate`, `classify_year_shorts`, `missing_history_suspects`, `detect_zero_basis_acquisitions`, `load_missing_history`, `synthesize_openings`, `stale_missing_history_entries`, `MISSING_HISTORY_FILE`, `draft_purchases`, `detect_superficial_loss_warnings`, `_walk_key`, `walk_journal_symbols`, `detected_journal_symbols`, `journal_leg_key`, `journal_targets`, `_journal_line_symbols`, `JournalDays`, `_journal_trade_day`, `_opposite_trades`, `books_journal_days`, `undeclared_journal_days`: finding sales with no purchase; the project file and the openings synthesized from it; purchase drafts and loss-window warnings; the walks' order, where a journal between a security's two lines (a join of the run, a Questrade `journal_pair`, RBC's J~ reference on an RBC export, a `.tt` JOURNAL line, a legacy JOURNAL line) reads a day's buys and in-legs first on its days only (its legs' days and its own trades' day — its quantity, FROM bought and TO sold; a TOBASE line names none, and the run names the `.tt` line for a day that looks like one).
- `src/taxjson/bin/taxjson_missing_history.py` — `main`, `_print_section`, `_print_zero_section`, `_write_purchases`: the `taxjson-missing-history` tool.
- `src/taxjson/bin/taxjson_run.py` — `cmd_find_missing_history`, `_missing_history_suspects`, `_year_short_rows`, `_refuse_unknown_missing_history_accounts`: `taxjson find-missing-history` and the run's use of the file.
- `src/taxjson/lib/phantom_holdings.py` — `missing_history`: the old module name, kept as an alias.
- `src/taxjson/lib/option_close_check.py` — `unbacked_option_closes`, `unbacked_option_close_messages`: option rows the broker marks closing that the books cannot back.

## The gains engines

`core.py` holds the transaction model and both engines. The Canadian engine
pools shares at adjusted cost base per security across taxable accounts and
applies the superficial-loss rule (with sheltered and affiliated accounts in
view). The US engine (experimental) keeps FIFO lots, per account when asked,
and applies the wash-sale rule. Both handle splits, renames, option exercise
and assignment, and option replacement.

- `src/taxjson/lib/core.py` — `TaxTransaction`, `load_transactions`, `coerce_transaction_row`, `canonical_date`, `require_computable_rows`, `SYMBOL_REQUIRED_ACTIONS`, `carry_row_marks`, `TaxRules`, `CanadaTaxRules`, `USATaxRules`, `get_tax_rules`, `compute_gains`, `find_replacements_in_window`, `make_gain_entry`, `detect_option_replacement_matches`, `_AssignPremiumLedger`, `disposition_groups`, `is_option_symbol`, `held_more_than_one_year`, `not_a_purchase`, `IN_KIND_CONTRIBUTION_TYPE`: the transaction model and loading (every JSON row goes through `coerce_transaction_row`: types, finite numbers, dates written YYYY-MM-DD); the computation's input contract (`require_computable_rows`, called by both engines' `compute_gains` and by `prepare_books`: a trade without its amount, an empty date or symbol, an action the engines do not book); the two engines (search `class CanadaTaxRules`, then its `compute_gains`); the US lot and wash-sale machinery; option replacement checks, assignment premiums, disposition grouping and option helpers.
- `src/taxjson/lib/numeric.py` — `D`, `round_half_up`, `round_floats`: decimal arithmetic for money.
- `src/taxjson/lib/loss_overrides.py` — `parse_line`, `read_project`, `write_state`, `read_state`, `flags`, `plan`, `summarize`, `stamp_entries`, `gather`, `problems`, `spelling_hint` (a no-match line: how the books spell the sale — another listing of the root, a ticker.map spelling, a date a few days off), `map_view`, `describe`, `warning_message`, `positions`, `STATE`: filing positions against the loss rule (`.tt` ALLOWLOSS lines, CA-SL-18 / US-WASH-25): the line, the run's `work/loss_overrides.json` and the `--loss-overrides` flag every engine run of the books gets (`src/taxjson/lib/pipeline.py` — `add_loss_override_args`, `loss_overrides_from_args`, `GainsRequest`), the sale each line names (`plan`, in the engine's order), what the rule would have denied (the engines record it, `loss_overrides` in a gains file, `loss_override` on the sale's rows), and the views: the run's Warning and its stop (`src/taxjson/bin/taxjson_run.py` — `_read_loss_overrides`, `_loss_override_flags`, `_say_loss_overrides`), `sum` and `wash-sales` (FILING POSITIONS), the checklist (`src/taxjson/lib/checklist.py` — `d_filing_positions`); the per-row note (`note_text`) on the return forms (`src/taxjson/bin/taxjson_form_export.py` — `_filing_position`, `_merge_positions`), in `audit` (`src/taxjson/bin/taxjson_audit.py` — `build_event`) and the explain traces (`src/taxjson/lib/trace_format.py` — `filing_position_text`), the US radar (`src/taxjson/bin/taxjson_wash_radar.py` — `_us_engine_losses`), `taxjson carryover` (`src/taxjson/bin/taxjson_carryover.py` — `filing_positions`) and `taxjson handoff` (`src/taxjson/bin/taxjson_run.py` — `_handoff_positions`).
- `src/taxjson/lib/wash_scope.py` — `scope_note`, `scope_lines`, `advisory_lines`: what the planning verdicts can and cannot see, per country.

## The gains run and the wash passes

`pipeline.py` is the one definition of a gains run: load-side preparation
(transfers, missing-history openings), the country's engine options (option
premium timing, US per-account lots), then the engine. `taxjson-gains` is its
command line. After each account has gains, a second pass re-runs them with
the other accounts in view (cross-account superficial loss); in Canada the
taxable accounts run as one blended pass and are split back per account.

- `src/taxjson/lib/pipeline.py` — `run_gains`, `GainsRequest`, `prepare_books`, `engine_options`, `option_timing_from_settings`, `option_timing_flags`, `place_retro_wash_adjustments`, `annotate_inventory_multipliers`, `declared_multipliers`, `tt_json_path`: one gains run; US retroactive wash adjustments and contract multipliers.
- `src/taxjson/bin/taxjson_gains.py` — `main`, `_request`, `_suggest_missing_history_and_exit`, `write_traces_file`: the `taxjson-gains` tool.
- `src/taxjson/bin/taxjson_split_gains.py` — `split_for_account`, `main`: splits a blended gains run back into per-account files.
- `src/taxjson/bin/taxjson_run.py` — `stage_wash_pass`, `stage_blended_wash_pass`, `_blend_conservation_gaps`, `_wash_flags`, `_render_wash_outputs`, `_record_wash_inputs`: the wash passes in a run, and the record of what each wash file was built from (`work/.wash_inputs.json`, for the stale check).

## Per-account reports

Each account ends with a `reports/<name>.sum` text report and its JSON twin:
gains, income, option summaries and holdings. Cross-account reports (covered
calls, long options, the wash radar, cross-listing lint) and the fee report
come after.

- `src/taxjson/bin/taxjson_sum_gains.py` — `summarize_gains`, `format_report`, `load_gains_data`, `main`: the gains section of a `.sum`.
- `src/taxjson/bin/taxjson_sum_income.py` — `summarize_income`, `format_report`, `load_income_data`, `main`: the income section.
- `src/taxjson/bin/_option_gains_report.py` — `process_data`, `load_inputs`, `main`: shared body of the two option reports.
- `src/taxjson/bin/taxjson_ccd_gains.py` — `process_data`, `main`: short calls (covered calls) by underlying.
- `src/taxjson/bin/taxjson_leaps_gains.py` — `process_data`, `main`: long options by underlying.
- `src/taxjson/bin/taxjson_export.py` — `render_report`, `render_holdings_toml`, `process_data_report`, `main`: holdings as a text report or a TOML snapshot.
- `src/taxjson/bin/taxjson_fees.py` — `aggregate`, `metrics`, `render_text`, `render_json`, `main`: the fee report by broker (`taxjson-fees-sum`).
- `src/taxjson/lib/report_model.py` — `build_account_report`, `resolve_gains_files`, `stale_wash_inputs`, `record_wash_inputs`, `render_table`, `align_columns`, `fmt_money`: shared report pieces and the account report JSON; which gains file a report reads and whether the wash-adjusted one is stale (its own books, and every member of its cross-account pass, from `work/.wash_inputs.json`).

## Output style and messages

All human output follows docs/output-style.md: wrapped prose, labelled
messages (Warning, Note, Error, ATTENTION), aligned tables. Stage messages are
captured during a run and re-worded for the console.

- `src/taxjson/lib/out.py` — `wrap`, `message`, `warn`, `note`, `attention`, `error`, `fail`, `fit_table`, `Doc`, `lint`, `console_lint`, `WIDTH`, `MAX_WIDTH`, `width`, `show`, `show_blocks`, `join_blocks`, `settle`, `settling_streams`: the house style; the style checks tests use; the wrap width (`TAXJSON_WIDTH`; 120 piped, the terminal's up to 160); a message printed with its lines flush-left and the one blank line a multi-line message owes its destination.
- `src/taxjson/lib/stage_msg.py` — `emit_line`, `say`, `reword`, `console_lines`, `is_continuation`, `split_message`: stage messages and the run console.
- `src/taxjson/lib/cli_diag.py` — `warn`, `error`, `note`, `read_text_utf8`, `write_text_atomic`, `InputReadError`: stderr diagnostics and safe reads and writes for tools.
- `src/taxjson/lib/trace_format.py` — `render_gain_block`, `render_report_trace`, `render_summary_table`, `render_document_header`: the per-gain trace boxes.
- `src/taxjson/lib/install_hint.py` — `extra_hint`, `INSTALLER`, `NOT_ON_PYPI`: the install lines the CLI prints.
- `docs/output-style.md` — `Width`, `Messages`, `The run's console`, `Numbers, dates`: the style rules.

## Row listings

The listing commands read the native (before base-currency) per-account files
and print rows in .tt style, filtered by period, account, symbol and action.

- `src/taxjson/bin/taxjson_run.py` — `_run_tx_view`, `cmd_transactions`, `cmd_dividends`, `cmd_dil`, `cmd_buysell`, `cmd_roc`, `cmd_gains`, `cmd_leaps`, `cmd_fees`, `cmd_transfers_view`, `_view_window`, `_require_books`: `events`, `divs`, `dil`, `trades`, `roc`, `gains`, `leaps`, `fees` and `transfers` (the TRANSFER evidence); the shared period filter and the "run first" check.

## Totals and positions

The `*-sum` commands total one row type per security or per year. `list` and
`shares` show positions from the canonical books (after ticker.map and
base-currency conversion, wash-adjusted where built).

- `src/taxjson/bin/taxjson_run.py` — `cmd_divs_sum`, `cmd_dil_sum`, `cmd_roc_sum`, `cmd_trades_sum`, `cmd_fees_sum`, `cmd_ccd_sum`, `cmd_leaps_sum`, `cmd_winners`, `cmd_positions` (`_list_positional_date`: `list [ACCOUNT] [YYYY-MM-DD]`), `cmd_shares`, `_box18_fractions`: totals by type; option totals and the ranked winners and losers; `list` and `shares`.

## Summaries and estimates

`sum` prints the year's summary across accounts (gains, income, by form
line). `estimate` and `amt` estimate the tax and the Canadian minimum tax;
`instalments` schedules Canadian instalments; `fx-cash` computes FX gains on
foreign cash; `stats` is a trader's win and loss view.

- `src/taxjson/bin/taxjson_run.py` — `cmd_summary`, `_section_1256_gain`, `cmd_estimate`, `cmd_amt`, `_tax_estimate_result`, `_print_tax_estimate`, `cmd_instalments`, `_instalment_config`, `cmd_fx_cash`, `cmd_stats`: `sum`, `estimate`, `amt`, `instalments`, `fx-cash` and `stats`.
- `src/taxjson/lib/tax_estimate.py` — `estimate_canada`, `estimate_usa`, `_amt_canada`, `ca_amt_carryover`, `apply_vintage`, `CA_FED_BRACKETS`: brackets, credits and the AMT, by rate year.
- `src/taxjson/lib/amt_report.py` — `build`, `render`, `render_recorded`: the AMT page.
- `src/taxjson/bin/taxjson_instalments.py` — `build`, `render`, `required_schedule`, `interest_and_penalty`, `PUBLISHED_RATES`: instalment schedule, interest and penalty.
- `src/taxjson/bin/taxjson_fx_cash.py` — `build_ledger`, `apply_jurisdiction`, `render_report`, `unreliable_status`, `CA_EXEMPTION`: FX gains on foreign-currency cash, the default ledger (v1: trades and income only, flagged NOT RELIABLE everywhere).
- `src/taxjson/lib/fx_cash_v2.py` — `build`, `headline`, `render`, `TOL`, `LABEL`: the opt-in ledger v2 (debt per broker account, reconciliation to statement balances, refusals); `src/taxjson/lib/cash_events.py` — `collect`, `parse_line`, `read_project_lines`, `pair_internal`, `Books`, `FORMS`: the cash-event book it reads (the `.tt` lines `FXCONV`, `CASHMOVE`, `CASHOPEN`, `CASHBAL`, `CASHBOOK` (`complete`), and each broker's `ib_cash_events`, `rbc_cash_events`, `kraken_cash_events`, `coinbase_cash_events`, `questrade_cash_events` in `src/taxjson/lib/brokerages/questrade.py`; `READERS` names them, and `Books.unread` the books of a broker with none, which v2 refuses until declared complete); `src/taxjson/bin/taxjson_run.py` — `_fx_cash_status`, `_fx_cash_v2_doc`, `_fx_cash_ledger_choice`, `_fx_cash_carry`, `_fx_sum_item`: which ledger, the status `sum` / `checklist` / the run hook show, and the prior year's close (`fx_cash_v2` in the close-year record).
- `src/taxjson/lib/trade_stats.py` — `compute`, `classify`, `written_option_trades`, `CLASSES`: win and loss statistics.

## Checks before filing

The checklist (`taxjson checklist`) is every step from install to filing
in ten sections, each step proved by a command or marked from the
project's files; outside a project it prints the steps as a guide. `sanity` compares the books against broker positions reports;
`check-dates` checks every trade time against market hours; `edge-cases`
lists rows whose treatment turns on a boundary.

- `src/taxjson/lib/checklist.py` — `STEPS`, `US_STEPS`, `DETECTORS`, `evaluate`, `Ctx`, `Result`, `input_fingerprint`, `record_input_fingerprint`, `inputs_changed`, `load_state`, `set_override`, `_SAVED_ID_MIGRATION` (a mark saved in checklist.json under the tips item's former id is read as the tips item's), `d_journals`, `d_renames`: the checks, one detector per check; the git checks (`d_inputs_committed`, `d_lock_committed`) go through `_git` (hooks and fsmonitor off) and `_git_status`, and are blocked by `_git_refusal` when the repository's own config names a command (`_GIT_COMMAND_KEYS`: a filter, a diff textconv); whether inputs changed since the last run, and saved marks. The list itself: `SECTIONS`, `items` (each `Item`'s section, title, `Cmd` commands, how and why; `_spec` holds them in order), `item_ids`, `item_meta`, `STEP_RULES` (the steps no detector proves: `s_configure`, `s_transfers`, `s_ticker_map`, `s_format`, ... — from the project's files and `reports/run_summary.json`, `src/taxjson/lib/first_run.py` — `collect`, `SUMMARY_FILE`), `_before_run`, `next_result`, `render`, `render_guide`, `to_json`, `guide_json`, `SCHEMA_VERSION`, `named_commands`, `EXCLUDED` (every user-facing command named by an item or excluded with its reason; `tests/test_checklist_items.py` checks both ways).
- `src/taxjson/bin/taxjson_run.py` — `cmd_checklist`, `_checklist_config` (taxjson.toml read leniently when the strict loader refuses it: the configure item's finding, and every check blocked; `src/taxjson/lib/checklist.py` — `Ctx`, `CONFIG_FIRST`), `_checklist_walk`, `cmd_sanity`, `_sanity_items_from_config`, `_sanity_print_extras`, `cmd_check_dates`, `cmd_edge_cases`, `cmd_spinoffs`, `cmd_splits`: `checklist` (and its interactive walk), `sanity`, `check-dates`, `edge-cases`, `spinoffs` and `splits`.
- `src/taxjson/lib/positions_reports.py` — `read_positions`, `detect_positions`, `PositionsReport`, `PositionRow`, `positions_only`: broker positions reports (IB, RBC, TOML holdings).
- `src/taxjson/lib/positions_check.py` — `compare_cost`, `positions_on`, `income_share_mismatches`, `load_inventory`: cost and dated-position checks for `sanity`.
- `src/taxjson/lib/export_coverage.py` — `find_gaps`, `account_gaps`, `open_positions`, `held_at_broker` (a broker's position counts as far as the account's books hold it), `closed_later` (the account's later rows of any source, `.tt` included, close a position), `next_activity` / `list_command` (the checklist's line: a count, `taxjson list <account> <end>` and the next activity a later file records), `file_source_id` / `row_source_ids` / `add_source` / `source_broker` (a book row's export, by the masked name the parse writes), `_book_renames` (sidecar and corporate-action rows through the books' renames), `info_message`, `ib_statement_end`, `webull_export_end`, `message`, `question_text`, `detail`, `Gap` (`key`: account|broker|end; `expired`: options that expired in the gap), `GRACE_DAYS`, `LAST_ROW_SLACK`: a broker whose exports for an account end before the year end (today in the running year) while it holds positions there; the run's Warning (`src/taxjson/bin/taxjson_run.py` — `_say_export_coverage`, `_checklist_answered`, `_mark_answers`), the checklist's export-coverage step (`src/taxjson/lib/checklist.py` — `d_export_coverage`; a `Result` with `question` set is answered by a DONE mark for the `answers` keys it recorded: `question_answers`, `apply_override`, `QUESTION_STEPS`, `option_keys`).
- `src/taxjson/lib/check_dates.py` — `analyze`, `render`, `check_trade_time`, `asset_class`: `taxjson check-dates`.
- `src/taxjson/lib/edge_cases.py` — `analyze`, `render_text`, `year_straddles`, `windows_across_year_end`, `calls_in_windows`: `taxjson edge-cases`.

## Audit, wash sales, explain and tax logic

`audit` traces every taxable disposition from the broker row to the reported
gain. `wash-sales` lists superficial losses or wash sales with the replacement
that caused each. `taxjson-explain` prints a calculation trace for one gain.
`tax-logic` prints every rule the code applies, with the project's settings
filled in; it is the spec the code and tests are checked against.

- `src/taxjson/bin/taxjson_audit.py` — `main`, `build_event`, `render_event`, `render_reconciliation`, `build_source_index`, `merge_lot_records`: the audit.
- `src/taxjson/bin/taxjson_run.py` — `cmd_audit`, `_audit_source_files`, `_merge_audit_json`, `cmd_wash_sales`, `_explain_wash_sales`, `cmd_tax_logic`: `audit`, `wash-sales` and `tax-logic`.
- `src/taxjson/bin/taxjson_explain.py` — `main`, `print_trace`, `gain_matches`, `fmt_summary`: the `taxjson-explain` tool.
- `src/taxjson/lib/tax_logic.py` — `Rule`, `catalog`, `sections`, `render`, `_canada`, `_usa`, `PARTITION_RULES`, `NON_RULE_SETTINGS`: the rule statements with their ids.

## Planning before you trade

These views answer "what happens if I trade now": the wash radar shows each
holding's superficial-loss or wash-sale status as of a date; buy-check and
sell-check answer for one symbol; harvest shows unrealized gains at current
prices; tips gives tax-efficiency advice for next year (for example a
Canadian dividend payer held through its US listing); watch reports changes
since the last look (cron-able); option-boundary lists written options that
straddle a year end.

- `src/taxjson/bin/taxjson_wash_radar.py` — `main`, `_render_text`, `_us_engine_losses`, `_advisory_category`, `_CA_DEFINITIONS`: the wash radar.
- `src/taxjson/bin/taxjson_run.py` — `cmd_wash_radar`, `_radar_config`, `_radar_engine_args`, `cmd_buy_check`, `cmd_sell_check`, `_wash_class_context`, `cmd_harvest`, `cmd_watch`, `cmd_tips`, `cmd_option_boundary`: `wash-radar`, `buy-check`, `sell-check`, `harvest`, `watch`, `tips` and `option-boundary`.
- `src/taxjson/lib/map_hygiene.py` — `symbol_root`, `listing_names`, `pair_verdict`, `APART` (a same-root pair is a candidate unless the exports show it apart — a receipt, different companies), `sightings`, `map_gaps`, `MapGap`, `gap_reason`, `unused_rules`, `UnusedRule`: the listing helpers `tips` and the map checks share; the listing pairs the map does not answer (MAP-GAP, a pair to verify: TOBASE or DISTINCT) and the rules no symbol of the books reaches (root-aware: an option's underlying and a rename chain keep a rule live) that `ticker-map --suggest` lists.
- `src/taxjson/bin/taxjson_safe_to_sell.py` — `main`, `_STATUS`: one line per taxable position: may it be sold at a loss today.
- `src/taxjson/bin/taxjson_harvest.py` — `main`, `load_positions`, `load_radar`, `_recovery_schedule`, `_days_to_long_term`: the harvest view.
- `src/taxjson/bin/taxjson_watch.py` — `diff_radar`, `diff_harvest`, `flatten_radar`, `load_state`, `render_report`: the change detector.
- `src/taxjson/lib/option_boundary.py` — `write_lots`, `straddling`, `expired_open`, `filed_locks`, `WriteLot`, `project_question_rows`, `question_message`, `question_detail`: written options across a year boundary, and the transition question (a contract written before `option_grant_timing_since` closed this year, CA-OPT-11) that `taxjson run` (`src/taxjson/bin/taxjson_run.py` — `_say_option_transition`), and the checklist (`src/taxjson/lib/checklist.py` — `d_option_boundary`) ask.
- `src/taxjson/bin/taxjson_lint_crosslistings.py` — `analyze`, `venue_splits`, `main`: cross-listed holdings the radar would see as two.
- `src/taxjson/lib/price_chain.py` — `fetch_prices`, `fetch_option_prices`, `PriceQuote`, `yf_symbol_for`, `latest_rate`: current prices (IBKR, then yfinance, then the cache).
- `src/taxjson/lib/price_chain.py` — `close_on`, `DayClose`, `OfflineCloseMissing`: one day's close (the close cache, then Yahoo's history) for an in-kind move's value.

## Filing forms and slips

`form-export` renders gains into filing shapes (Schedule 3 lines for Canada,
Form 8949 and TXF for the US). `t1135` helps with the foreign property form.
`reconcile-slips` compares the broker's T5008 or 1099-B slips with the
computed dispositions; `slip-audit` (Canada) compares the T5 and T3 slips
with the books' income. `carryover` keeps the loss carry-forward ledger.

- `src/taxjson/bin/taxjson_form_export.py` — `main`, `build_schedule3`, `schedule3_line`, `build_8949`, `build_txf`, `filing_lines`: the form renderers; `write_csv` (`--csv`) writes each cell through `spreadsheet_cell` (a formula-looking text cell gets a leading quote).
- `src/taxjson/bin/taxjson_t1135.py` — `build_report`, `render_report`, `walk_costs`, `classify_country`, `FILING_THRESHOLD`: T1135 cost amounts by country.
- `src/taxjson/bin/taxjson_reconcile_slips.py` — `reconcile`, `load_slip`, `load_computed`, `render`, `SlipRefused`: slip reconciliation.
- `src/taxjson/lib/slip_audit.py` — `audit`, `load_slips_file`, `load_books`, `ib_slips`, `report_identity`, `annual_average`, `_audit_group`, `_match_payments`, `_securities`, `_suggest_cgd`, `_suggest_roc`, `question_keys`, `template`, `render`: `taxjson slip-audit` — the slips in inputs/slips/ (slips.toml, IB's dividends reports) against the books' income per account, broker account, currency and box; the suggestions; the checklist's t5-t3 step (`src/taxjson/lib/checklist.py` — `d_t5_t3`).
- `src/taxjson/lib/cra_slips.py` — `parse_slips`, `parse_text`, `read_pdf`, `pdf_paths`, `broker_of_issuer`, `issuer_aliases`, `alias_of_issuer`, `book_groups`, `_bare_groups`, `place`, `drop_duplicates`, `plan_import`, `table`: T5 / T3 PDFs downloaded from CRA My Account (`slip-audit --import-cra`, `src/taxjson/bin/taxjson_run.py` — `_slip_audit_import_cra`): read with pdftotext (`pdf_text`: the absolute path after `--`, its text capped at `PDF_TEXT_MAX`; each slip from its own slip line: the slip line and box rows only), a T5's broker from its issuer (the broker's whole name, or a carrying dealer the shipped `src/taxjson/data/slip_issuers.toml` names; broker accounts with no income rows from the parse metadata, `src/taxjson/lib/slip_audit.py` — `broker_account_files`), placed in an account, broker account and fund by the books' payments, each slip once (a duplicate dropped, an amended slip replacing its original), written as [[slip]] tables with a salted broker key (`src/taxjson/lib/slip_audit.py` — `key_salt`, `broker_key`, `resolve_keys`, `comment_out_tables`); `tjs redact` replaces the key in its copy (`src/taxjson/bin/taxjson_redact.py` — `_redact_broker_keys`).
- `src/taxjson/data/slip_issuers.toml` — `alias`, `broker`, `issuer`, `source`: the shipped issuers that are a broker's carrying dealer (CI Investment Services for Webull Canada), read only by `issuer_aliases`; an issuer carrying a broker's whole name needs no entry.
- `src/taxjson/lib/ib_dividends.py` — `read_report`, `is_dividends_report`, `component_category`, `Payment`: IBKR's dividends report (U*.YYYY.dividends.csv), payment by payment with its T5/T3 split; the holder's name is never read.
- `src/taxjson/bin/taxjson_carryover.py` — `build_canada_ledger`, `build_usa_ledger`, `load_claimed`, `lock_figure`, `render`: the carryover ledger.
- `src/taxjson/lib/carryforward.py` — `resolve_losses`, `resolve_amt`, `record_block`, `lock_block`, `handoff_issues`: carry-forwards from one year to the next.
- `src/taxjson/bin/taxjson_run.py` — `cmd_form_export`, `cmd_t1135`, `cmd_reconcile_slips`, `cmd_slip_audit`, `cmd_carryover`, `_taxable_gains_argv`: the commands.

## Filed-year lock and year hand-off

`close-year` snapshots a filed year's figures; every later run and
`check-filed` recompute the year and report drift. `handoff` records what a
closed year carried forward and checks that the next year's project starts
from exactly that.

- `src/taxjson/bin/taxjson_filed.py` — `write_snapshot`, `recompute_year`, `diff_snapshot`, `project_locks`, `lock_for_year`, `aggregates_from_gains`: the filed-year lock.
- `src/taxjson/bin/taxjson_run.py` — `cmd_close_year`, `cmd_check_filed`, `_check_filed_years`, `_carryforwards_for_lock`, `_locked_year_flags`, `cmd_handoff`, `_prior_record_path`, `_handoff_gains_flags`: `close-year`, `check-filed` and `handoff`, and the drift check every run makes of each filed year.
- `src/taxjson/lib/handoff.py` — `snapshot`, `check`, `render`, `validate_record`, `straddlers`, `load_filed_dispositions`: the year-to-year record and its check.

## Redact

`redact` strips account numbers, names and contact details from a broker
export while keeping every row's shape, so a file can be shared as a parser
sample or a bug report. It is a best-effort pattern matcher.

- `src/taxjson/bin/taxjson_redact.py` — `redact_file`, `redact_text`, `redact_tree`, `compile_patterns`, `Pseudonyms`, `load_denylist`: the redactor; wallet addresses `_WALLET` (pseudonymised when `_plausible_wallet`), leftovers `_WALLETISH` / `_wallet_like` (review); the review pass `_review` (`_caps_name`, `_LAST_FIRST`); ids after a broker name `_BROKER_ACCOUNT`; the project copy walks `inputs/` with `_walk_inputs` (a file link leaving it is skipped, `_inside`).
- `src/taxjson/bin/taxjson_run.py` — `cmd_redact`: `taxjson redact`.

## Configuration: taxjson.toml, init, format and migrate

taxjson.toml holds `[settings]` (country, year, tax date basis ...) and one
table per account. Every reader goes through the same checks. `init` writes
a new project from a template; `format` re-renders an existing file into the
template keeping the user's values; `migrate` folds an old project's separate
files into ticker.map and taxjson.toml.

- `src/taxjson/bin/taxjson_run.py` — `load_config`, `validate_config`, `_normalize_settings`, `_warn_config_tables`, `_refuse_bad_account_types`, `cmd_init`, `_render_init_config`, `cmd_format`, `_backup_config`, `cmd_migrate`: reading and checking taxjson.toml; `init`, `format` and `migrate`.
- `src/taxjson/lib/config_check.py` — `settings_problems`, `account_type_problems`, `account_name_problem`, `ACCOUNT_TYPES`, `ACCOUNT_KEYS`, `RETIRED_SETTINGS`: the checks every config reader applies.
- `src/taxjson/lib/config_template.py` — `SETTINGS_SPEC`, `ACCOUNT_SPEC`, `TABLES`, `render_init`, `format_config`, `scaffold_document`, `Key`: every key taxjson reads, documented per country.
- `src/taxjson/lib/migrate.py` — `plan`, `apply`, `Plan`, `legacy_files`, `LEGACY_FILES`, `MigrateError`: moving old files into the new places.
- `src/taxjson/lib/safe_write.py` — `write_user_file`, `write_atomic`, `atomic_open`, `backup_copy`, `file_lock` (a link at the lock name replaced, `LockLinkError` when it cannot be), `OutsideLinkError`: writes that never follow a planted symlink, each through a temp file of its own (overlapping writers never share one), with a backup; `file_lock` serializes a read-modify-write. Generated state in `work/` goes through `write_atomic`; `tests/test_fix_issues_safe_writes.py` lists the reviewed direct writes left in the core.
- `src/taxjson/lib/tomlcompat.py` — `tomllib`: the tomllib or tomli import.

## The Canada and USA partition

Canadian and US rules never mix. One module resolves a project's country and
owns the tables saying which settings, config tables, flags, commands and
project files belong to which country. `scripts/check_tax_rules.py` checks
that the tables are complete.

- `src/taxjson/lib/country.py` — `settings_country`, `canonical_country`, `CountryError`, `SETTING_COUNTRY`, `CONFIG_COUNTRY`, `FLAG_COUNTRY`, `COMMAND_COUNTRY`, `PROJECT_FILE_COUNTRY`, `config_country_problems`, `check_engine_allowed`, `refuse_foreign_flags`, `default_tax_date`, `basis_pooled_across_accounts`: the one country resolver; the ownership tables (settings, config tables, flags, commands, project files); the checks built on them; per-country defaults.
- `src/taxjson/bin/taxjson_run.py` — `_country`, `_tax_date`, `_is_us`, `_refuse_other_country_books`: how the CLI reads them.

## Markets, calendars and dates

Venue suffixes, currencies and the security lists taxjson cannot read from an
export live in one data file, overridable from ticker.map. Settlement dates
use each market's holiday calendar and the T+3, T+2, T+1 history.

- `src/taxjson/lib/markets.py` — `data`, `overrides`, `suffix_of`, `suffix_currency`, `is_canadian_listing`, `split_share_roots`, `contract_size`, `usd_unit_listing`: market reference data (and the convention for a Canadian-listed fund's US-dollar units).
- `src/taxjson/data/markets.toml` — `venues`, `currency_suffix`, `usd_unit_class`, `ib_venues`, `kraken_assets`: the shipped defaults.
- `src/taxjson/lib/market_calendar.py` — `add_settlement_days`, `is_settlement_day`, `is_trading_day`, `nyse_holidays`, `tsx_holidays`: settlement calendars.
- `src/taxjson/lib/dates.py` — `settlement_date`, `settlement_lag_days`, `date_to_epoch`, `market_of`, `last_trade_date_settling_by`: date helpers for the radar and planning views.

## The fetch plugin

The core holds no broker API client and reads no broker credential.
`taxjson fetch` finds installed fetchers through the `taxjson.fetchers` entry
point; the separate taxjson-fetch package provides Questrade and IBKR Flex
downloads.

- `src/taxjson/lib/fetchers.py` — `discover`, `Fetcher`, `FetchRequest`, `ENTRY_POINT_GROUP`, `add_fetcher_arguments`, `listing`: the core side of the plugin contract.
- `src/taxjson/bin/taxjson_run.py` — `cmd_fetch`, `_no_fetcher_exit`, `_fetch_plugin_note`, `_fetch_brokerages`: `taxjson fetch`.
- `packages/taxjson-fetch/src/taxjson_fetch/plugin.py` — `BrokerFetcher`: the object the core loads.
- `packages/taxjson-fetch/src/taxjson_fetch/command.py` — `run`, `_run`, `_merge_csv_text`, `_questrade_token_file`, `_qt_open_session`, `_questrade_token_write`, `_flex_lost_dates`, `_qt_trim_file`: the fetch command (one per project under `work/.fetch.lock`; the token's read, refresh and save under a lock beside the token file; merging a re-fetch into the existing CSV; `--trim-overlap` backups through `backup_copy`).
- `packages/taxjson-fetch/src/taxjson_fetch/api.py` — `qt_refresh`, `qt_activities`, `qt_positions`, `flex_fetch`, `positions_to_holdings_toml`, `mask_account_number`, `write_private`: the Questrade and Flex API clients, and the owner-only atomic writer of fetched files.
- `packages/taxjson-fetch/pyproject.toml` — `taxjson.fetchers`: the entry-point registration.

## Release channels and install

`main` is the development line; a `vX.Y.Z` tag is a release. channels.json
names the release each channel (stable, beta) points at. The installer clones
a channel's release; `release.sh` cuts a tag after the full gate;
`promote.sh` moves a channel. docs/releasing.md has the steps.

- `install.sh` — `main`, `newest`, `named`, `vernewer`, `--channel`: the one-line installer.
- `channels.json` — `stable`, `beta`: where each channel points.
- `scripts/release.sh` — `## Unreleased`, `CHANGELOG.md`, `scripts/ci.sh`, `scan_notes`, `gh release create`: cuts a release (CHANGELOG heading, version bump in both packages, full gate, tag, push, the GitHub release with scanned notes).
- `scripts/promote.sh` — `die`, `channels.json`, `ci_gate`, `undo_promote`: points a channel at a release (main = origin/main, the tag on origin/main, green CI forward).
- `scripts/channels.sh` — `taxjson channels`: the channel page from a checkout.
- `src/taxjson/lib/channels.py` — `read_channels`, `parse_channels`, `release_tags`, `status`, `render`, `dev_checkout`: `taxjson channels`, and the checkout `promote` and `deploy` use.
- `src/taxjson/bin/taxjson_run.py` — `cmd_channels`, `cmd_promote`, `cmd_deploy`, `_run_script`, `_RELEASE_CMDS`: the release commands.
- `scripts/dev-setup.sh` — `venv/bin/pip install`, `install_hook`, `--hook-only`: developer setup (venv, editable installs, the pre-push hook in the clone's own hooks folder).

## The CI gate and repository checks

`scripts/ci.sh` is the gate: lint, consistency, tax-rules, PII scan, the full
suite (core and the fetch plugin) and the fuzzers. A push must see its result
line PASS. The pre-push hook scans what a push would publish.

- `scripts/ci.sh` — `stage`, `fuzz_run`, `--nightly`: the gate's stages.
- `scripts/check-consistency.sh` — `CHANGELOG`, `channels.json`: versions, CHANGELOG heading and channels agree.
- `scripts/check-pii.sh` — `main`, `report`, `amount_filter`, `sin_filter`, `entropy_filter`, `CRED_RE`, `--diff`: the personal-data and secret scan (tree, diff, messages).
- `.gitleaks.toml` — `useDefault`, `allowlists`: the allowlist of CI's gitleaks job (`.github/workflows/tests.yml`, job `secrets`).
- `scripts/hooks/pre-push` — `refuse`, `tag_refused`, `check-pii.sh`: the pre-push hook (the tag guard, then the PII scan).
- `scripts/check_tax_rules.py` — `main`, `collect`, `ownership_problems`, `read_ids`: every tax-logic rule has a test that cites it, and the country tables are complete.
- `tests/tax_rules/__init__.py` — `rule`, `rule_absent`: the test markers that cite rule ids.
- `tests/tax_rules/baseline-unpinned.txt` — `CA-`: the shrink-only list of rules without a test yet.
- `scripts/style/survey.py` — `main`: runs every command on the synthetic style projects and saves the output.
- `scripts/style/measure.py` — `measure`, `is_table`: ranks those captures by style problems.
- `scripts/mutation_audit.py` — `TARGET_FUNCS`, `find_sites`: the mutation harness (mutates engine files in place; run only with `--yes` and restore from git if interrupted).
- `scripts/mutation_triage.py` — `main`: groups mutation survivors for review.
- `.github/workflows/tests.yml` — `check-pii.sh`, `check_tax_rules.py`: the same gate on pull requests.

## Tests

Run the suite from `tests/` with `TAXJSON_WIDTH=0`. Engine changes are pinned
by property fuzzers and by tests that run both countries side by side.
Style tests build small synthetic projects and check every line printed.

- `tests/tax_rules/dual.py` — `gains_both`, `projects_both`, `cli_both`, `settings_for`, `tx`: one book or project run under both countries.
- `tests/_style.py` — `project`, `Project`, `assert_styled`, `assert_console`, `assert_labelled`: synthetic projects and output-style asserts.
- `tests/_tmpfiles.py` — `private_tmpfile`, `private_dir`: temp files each alone in a private folder, so a parser's sibling discovery never reads another test's or run's file.
- `tests/test_temp_isolation.py` — `TestNoSharedTempRoot`, `TestStrayLedgersInTheTempRoot`: no test writes into the shared temp root; stray Kraken ledgers in TMPDIR break nothing.
- `tests/test_engine_invariants.py` — `TestConservationFuzz`, `TestOrderInvariance`, `TestCraGoldenExamples`, `make_book`: engine fuzzing and golden examples.
- `tests/test_conservation.py` — `TestShareCountChecker`, `TestStrandedBasisChecker`: share and cost conservation checks.
- `tests/test_transfer_fuzz.py` — `make_transfer_book`: transfer fuzzing.
- `tests/test_settle_straddle_fuzz.py` — `build_book`, `conservation_gap`: settlement-straddle fuzzing.
- `tests/test_partition_foundation.py` — `TestOneCountryResolver`, `TestSettingOwnership`, `TestCommandOwnership`: the country partition.
- `tests/test_output_style.py` — `TestWidth`, `TestWrapAndMessages`: the output style.
- `tests/test_check_pii.py` — `TestTreeScan`, `TestDiffAndPush`: the PII scanner.
- `tests/test_knowledge_pack.py` — `TestArchitectureMap`, `TestReferences`: every path and symbol this map names still exists.
- `run_tests.sh` — `unittest`: runs the suite with the project venv.
