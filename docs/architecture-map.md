# Architecture map

Where the code for each part of taxjson lives, so a diagnosis or a change can
go straight to the right file. taxjson is a Python package: a Canadian
capital-gains toolkit (ACB, superficial loss, income, filing forms) built from
broker CSV exports, with an experimental US engine. The command is `taxjson`,
or `tjs` for short.

Several files are very large. `src/taxjson/bin/taxjson_run.py` (about 31k
lines) holds the whole CLI: every `cmd_*` handler, the `stage_*` functions of
`taxjson run`, and the argument parser. `src/taxjson/lib/core.py` (about 9.3k)
holds both gains engines. `src/taxjson/lib/brokerages/ib_extractor.py` (about
6k), `src/taxjson/lib/corp_actions.py`, `src/taxjson/lib/missing_history.py`,
`src/taxjson/lib/tax_logic.py` and `src/taxjson/lib/checklist.py` (about 4.4k to
5.2k each) and `src/taxjson/lib/brokerages/rbc_direct.py` (about 3k) follow. Do not read them top to bottom: search them for the function name
listed below ("def name("), then read around the hit.

Conventions used here:

- `src/taxjson/bin/taxjson_run.py` — `cmd_run`, `stage_account`: a command `foo-bar` is handled by its `cmd_<name>` function in this file (dashes as underscores), with a few older names (`events` is `cmd_transactions`, `trades` is `cmd_buysell`, `list` is `cmd_positions`, `sum` is `cmd_summary`); `_build_parser` maps each name with `set_defaults(func=...)`.
- Each stand-alone tool `taxjson-<tool>` is `src/taxjson/bin/taxjson_<tool>.py` with a `main()`; `taxjson run` calls them through `_cmd("taxjson-<tool>")`, usually in the same process. `src/taxjson/bin/taxjson_run.py` — `_subparser`: an unknown flag after a command is refused with that command's usage line (`main` parses with `parse_known_args`).
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

- `src/taxjson/bin/taxjson_run.py` — `main`, `_main`, `_build_parser`, `_COMMAND_GROUPS`, `_GroupedHelpParser`, `_dev_checkout_present`, `_MAINTAINER_GROUP`, `_enforce_command_country`, `_refuse_unknown_account`, `_split_command_segments`, `_command_index`, `_chain_accounts`, `_help_country`, `_failure_headline`, `_die`: the entry point, the parser and the grouped help page (the other country's commands hidden, and the Maintainer group outside a development checkout); the checks applied before every command; command chaining (`taxjson run sum`; `--` separates; an account, folder or symbol named like a command stays the command's argument); how a failed stage or bad input becomes one short message.
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

- `src/taxjson/bin/taxjson_run.py` — `cmd_run`, `_acquire_run_lock` (held through `safe_write.file_lock`), `_refuse_outside_dir_links` (a written folder linked outside the project stops every command: `_refuse_outside_links`, the project-level check `_main` runs before any command whatever reads its config, `_LINK_CHECK_FREE_CMDS` the commands it leaves alone, and `load_config`), `_loose_project_dirs` (the once-per-run warning about a folder other users can read), `needs_rebuild`, `_package_fingerprint`, `_package_files`, `stage_account`, `write_holdings_toml` (each account's holdings report, a crypto account's from its own books), `stage_wash_pass`, `stage_blended_wash_pass`, `stage_cross_reports`, `stage_fees`, `collect_diagnostics` (a blend mirror's message about another account's file tagged `[<account>]`: `_diag_block_account`), `echo_attention_lines`, `_first_run_summary`: the run, its one-run-per-project lock and the `--fast` cache rules; `stage_account` is the per-account chain (parse, corp actions, .tt files, transfer arrivals, merge, gains, raw holdings, the `.sum` report); then the passes across accounts; what each stage printed, folded into the console and the `.sum` DIAGNOSTICS section; the closing summary.
- `src/taxjson/lib/first_run.py` — `collect`, `render`, `render_blocks`, `uncovered_short_sales`, `engine_booking`, `zero_cost_positions`, `income_without_position`, `SUMMARY_FILE`: the "what to check next" counts at the end of a run; `engine_booking` reads the gains files so a sale is called "NOT in `taxjson sum`" only when they lack it (the run's mid-run note, before the wash pass, reads the plain ones: `prefer_wash=False`).
- `src/taxjson/bin/taxjson_run.py` — `cmd_run`, `_cmd_run`, `_run_concise`, `_say`, `_DETAILS_HIDDEN`, `_ECHO_LAST_ERROR`: `cmd_run` turns the one-line console on for the run and its stages (`lib/stage_msg.CONCISE_ENV`, without `--details` on a person's console) and calls `_cmd_run`, the run itself; `_say` prints a warning or note as one line (its `short` form, or the headline and the command its detail names), an error with its fix lines; `_DETAILS_HIDDEN` makes the closing line name `taxjson run --details`. `src/taxjson/lib/first_run.py` — `concise_items`, `CONCISE_BUDGET`: the closing block under "Before you trust these numbers", one line per finding, at most six lines.

## Input discovery and broker detection

Each account's folder under `inputs/` is scanned; every CSV is routed to a
parser by its content (header signature), then by its name prefix, never by
guessing. A file that matches two parsers or none stops the run with a
message naming the file.

- `src/taxjson/bin/taxjson_run.py` — `input_files`, `group_inputs_detailed`, `detect_broker`, `_report_detection`, `spreadsheet_inputs`, `_duplicate_input_files`, `_sweep_retired_exports`, `_warn_shared_broker_accounts`, `_source_brokerages`: the folder scan, the one line per file saying how it was read, and checks on duplicate, retired or shared exports.
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

- `src/taxjson/lib/brokerages/questrade.py` — `_qt_real_order` (a newest-first export's rows in real order, for the parser and `questrade_cash_events`), `QuestradeBrokerage`, `QtAccountContext`, `build_qt_account_context`, `_plan_qt_reversals`, `_detect_qt_ticker_changes`, `scan_code_uses`, `_plan_qt_journals`, `_journal_listing`, `journal_codes`: Questrade activity exports, including internal security codes and BRW currency journals (paired legs, the US-dollar line, codes learned from a journal leg).
- `src/taxjson/lib/brokerages/rbc_direct.py` — `RbcBrokerage`, `read_rbc_rows`, `classify_rbc_row`, `RbcAccountContext`, `build_rbc_account_context`, `_plan_reinvest_reversals`, `rbc_coverage_messages`, `is_holdings_export`, `RbcFormatError`: RBC Direct Investing; every row is classified by its activity label and event code; date coverage, holdings files and format refusals. Every lookup by company name (temporary codes, listings, spin-off parents) reads the name through `src/taxjson/lib/corp_actions.py` — `rbc_row_company` (the optional Symbol Description, else the trade's own Description).
- `src/taxjson/lib/brokerages/ib_extractor.py` — `IbBrokerage`, `parse_file`, `prepare_files`, `reconcile_files`, `resolve_unmatched_ca`, `ib_year_coverage`, `get_ib_settlement`, `_ib_market_trade_date`, `_ib_xfer_cancels`, `_ib_fold_refund`, `_ib_temp_folds`, `_map_names_temp`, `_project_map_names`, `_ib_fold_rows`, `_warn_stock_aliases`, `_security_name`: Interactive Brokers activity statements (every section of one CSV; a row's security name from the instrument on its own listing's market when several share its symbol); coverage gaps, settlement and trade dates, cancellations and fee refunds; IB's temporary time-stamped symbols folded onto their ticker (unless a ticker.map line names the stamped symbol), and one contract under two symbols (a dated RENAME hint).
- `src/taxjson/lib/brokerages/webull.py` — `WebullBrokerage`, `label_hits`, `_WEBULL_OPTION_RE`, `_deliverable_size`: Webull exports (both column layouts, matched by header label).
- `src/taxjson/lib/trade_cancel.py` — `pair_cancellations`, `is_trade_cancel`, `TRADE_CANCEL_TYPE`, `trade_cancel_what`: an IB `Ca` cancellation netted against its original (a partial one reduces it; later ones match what is left; a cancellation of the whole order after one of its executions' removes it in full: `overlaps`).
- `src/taxjson/lib/futures.py` — `settle_futures`, `is_plain_future`, `method_for`, `section_1256_kind`: plain futures booked on a settlement basis, per account in its conversion (`src/taxjson/bin/taxjson_convert_currency.py` — `process_transactions`, `stamp_rate`); each settled part records its native facts (`settle_native`) and Canada re-averages the closes across the taxable accounts in the engine (`pool_across_accounts`, `parse_settle_native`), a short position in its own pool (`engine_pool_key`; `src/taxjson/lib/core.py` — `_fold_short_futures_pools`).
- `src/taxjson/lib/futures.py` — `settle_futures_dicts`, `fee_basis`, `_own_fee`; `src/taxjson/bin/taxjson_fx_cash.py` — `_cash_legs`: ledger v2 dates each futures commission on its own fill.

## Crypto parsers

Kraken and Coinbase exports are read strictly: amounts must parse exactly, and
local times need a time zone. Withdrawals and deposits are kept as TRANSFER
custody evidence for the crypto sends step.

- `src/taxjson/lib/brokerages/kraken.py` — `KrakenBrokerage`, `header_kind`, `REQUIRED_BY_KIND`, `known_assets_of`, `_ledger_identity`: Kraken trades and ledger exports.
- `src/taxjson/lib/brokerages/coinbase.py` — `CoinbaseBrokerage`, `resolve_header`, `is_header_row`, `_cb_symbol`, `_HEADER_SYNONYMS`, `coinbase_row_total` (a row's Total, a blank one derived, for the cash reader and the crypto-sends pool): Coinbase transaction exports.
- `src/taxjson/lib/brokerages/_crypto_common.py` — `strict_money`, `utc_to_local`, `LocalTimezoneMissing`, `usd_value`, `warn_depeg`, `RowIds`: helpers both crypto parsers share (`RowIds`: one broker id on two different rows of a file is refused).
- `src/taxjson/bin/fill_crypto_prices.py` — `main`, `get_crypto_price`, `load_cache`, `yahoo_id`, `_implausible_yahoo_prices`: the `taxjson-fill-crypto` stage that prices crypto rows with no fiat value; `cached_price`, `_damaged_entry` (a cached 0, negative or non-number is a miss, looked up again); `_swap_pairs`, `_value_swaps_once` (a swap's two legs take one value: a stablecoin leg's par, else the received coin's close).

## Generic CSV, hand entry (.tt) and adding a parser

A broker with no parser can be read through a column mapping (`generic_*.csv`
plus its mapping). Hand-entered rows go in `.tt` text files (one transaction
per line), which `taxjson run` converts to JSON. A new parser registers in
`taxjson_brokerage.py`, adds a detector, and passes the conformance harness
against a synthetic fixture.

- `src/taxjson/lib/brokerages/generic.py` — `GenericBrokerage`, `mapping_path`, `_load_mapping`, `_check_keys`, `_check_trade_row`: the column-mapped importer.
- `src/taxjson/bin/taxjson_convert_tt.py` — `tt_to_json`, `parse_tt_line`, `parse_opening_line`, `json_to_tt_lines`, `compute_tt_id`, `main`: the .tt format in both directions; `tt_lines`: a .tt file's lines, split at a line feed only, for every reader of one.
- `src/taxjson/bin/taxjson_run.py` — `stage_account`, `_refuse_crypto_openings`: where an account's `.tt` files are converted; an OPENING line in a crypto account is refused.
- `src/taxjson/lib/check_tt.py` — `check_project`, `check_paths`, `check_file`, `parse_line`, `render`, `Problem`, `FileReport`, `_taxable_transfers`, `_opening_snapshots` (one snapshot date per symbol per account, `lib/opening.snapshots`), `_as_in_project`, `project_root`; `src/taxjson/bin/taxjson_run.py` — `cmd_check_tt`, `_tt_path_item`, `_tt_file`, `_check_tt_files_only`: `taxjson check-tt`, every `.tt` line through the run's own readers (the converter's parsers, then `read_declarations` and `read_tt_openings` across lines, and a taxable account's TRANSFER rows through the run's netting), every problem named with its file and line, errors (refused by the run) apart from warnings (booked as written); a file named by its path needs no project, and one the run reads for its account gets the account's checks; a relative FILE is found from the shell's folder, the project's, or its inputs folder.
- `src/taxjson/lib/tt_totals.py` — `read_diag`, `project_mismatches`, `Mismatch`, `tolerance`, `source_line` (an ACQUIRED line quoted as written): a `.tt` line whose total is not qty x price +/- fee (booked as written), read back from the stage's .diag; `src/taxjson/bin/taxjson_run.py` — `_echo_tt_totals` shows each on the console on every run and `--strict` stops on it; `src/taxjson/lib/checklist.py` — `d_run_clean` lists them.
- `src/taxjson/bin/taxjson_run.py` — `_echo_tt_bare_symbols`; `src/taxjson/bin/taxjson_convert_tt.py` — `_warn_bare_equity_symbol`, `_equity_account`: a `.tt` symbol without its market suffix on an equity account (the run passes `--equity`), shown on the console with file:line when the broker rows hold the suffixed listing.
- `src/taxjson/bin/taxjson_generate_parser.py` — `main`, `gate_sample`, `identity_findings`, `_call_claude`, `_call_gemini`: maintainer tool that drafts a parser from a sample CSV.
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
- `src/taxjson/bin/taxjson_ticker_map.py` — `apply_mapping`, `load_map_file`, `map_file_problems`, `merge_renames`, `bare_target_warnings`, `guard_option_listing_collisions`, `named_symbols`, `_parse_map_text`, `distinct_spellings`, `listing_spelling_notes`: the `taxjson-ticker-map` stage applied to a book; a bare US ticker beside a share listing also read as the US listing in a DISTINCT line (a rename-type line so written warned when the books hold the US listing: `src/taxjson/bin/taxjson_run.py` — `_say_listing_spellings`); the symbols a map's lines name (`lookups=True`: a lookup line's symbol too); a rename-type line joining an option or a future with shares, or two contracts, refused (`src/taxjson/bin/taxjson_convert_tt.py` — `_join_derivative_check`). A map with `map_file_problems` stops `taxjson run` and the wash checks (buy-check, sell-check, rebuy) alike: `src/taxjson/bin/taxjson_run.py` — `_refuse_map_problems`, `_wash_class_context` (`load_map_file` alone skips such a line with a warning).
- `src/taxjson/lib/renames.py` — `DatedRename`, `rename_events`, `apply_dated_renames`, `after_rename_row`, `RenameNeedsCountry`, `late_rows`, `declared_late`, `unresolved_late`, `unused_declarations`, `row_source`, `row_source_id`, `SOURCE_LABELS`, `KIND_CRYPTO`, `rename_hints`, `report`, `render`, `_walk_states`, `_walk_fifo`: renames as dated events and `taxjson renames` (each rename's source; the position and book cost each rename carried, replayed by the country's lot rule, `country.lot_rule`; applied in date order, to the accounts of the declaring kind, `late=` per account — `DatedRename.late_for`, the event's own only for the accounts that held OLD; `late=fold` re-books the rows the country's engine takes after the account's own rename row — `after_rename_row` compares `event_sort_key` keys, `ca_main` or `us_main`; OLD includes the raw spellings an undated rename maps onto it; a declaration that books nothing listed; the look-alike renames the exports' .diag hints show, suggested with the `.tt` line that books each).
- `src/taxjson/lib/dated_events.py` — `read_declarations`, `check_against_map`, `resolve_renames`, `project_renames`, `settle_journals`, `write_sidecars`, `journal_legs`, `effective_lines`, `apply_meta`, `tt_renames`, `rename_records`, `state_doc`, `read_state`, `legacy_note`, `home_accounts`, `plan_migration`, `account_kind`, `Journal`, `STATE`: the `.tt` JOURNAL and RENAME lines of every account (checked up front), a journal settled against the broker's own legs and booked as transfer evidence (work/<acct>_tt-journal_transfers.json), every declaration of a ticker change merged into one event (contradictions refused) and written into the run's effective map, `format-map`'s simulation of the run before and after a migration, and the record of every dated event (work/dated_events.state, each with its source and place).
- `src/taxjson/bin/taxjson_convert_tt.py` — `parse_journal_line`, `parse_rename_line`, `_EVENT_ACTIONS`: the two dated-event `.tt` lines (date first, never rows of the converted file).
- `src/taxjson/lib/journals.py` — `report`, `render`, `source_of`, `SOURCES`, `_Map`, `_broker_journals`, `_classify`, `FORMAT`: `taxjson journals` — every broker journal between two listings, per account, with how it was found and its state (joined with the map line that pools it, suggested with the lines that settle it, refused with the undo), read from work/cross_listings.state, the parsed exports and the effective map.
- `src/taxjson/lib/cross_listings.py` — `gather`, `analyze`, `shown_apart` (the exports' evidence that two same-root listings are NOT one security: a receipt, different companies), `map_lines`, `effective_map_text`, `joined_note`, `companies_differ`, `collisions`, `extract_words`, `collision_note`, `journal_wording`, `_explicit_journal`, `_journal_names_verdict`, `_trailing_form`, `_hub_partners_agree`, `business_days`, `read_state`, `state_problem` (why an existing state file reads as empty, or loses a record: its JSON, its format, each record list and record, `_records_problem`; `taxjson journals` and the checklist refuse it), `STATE_KEYS`: two listings joined by their transfer journal (the pairs left alone — a DISTINCT line, the user's map, two companies — recorded as `refused` for `taxjson journals`) (a broker's explicit journal pair by its legs' own names; the window in business days); a symbol naming two companies (a fund's US-dollar unit beside an NYSE stock) and the EXTRACT line that separates them; a Questrade currency journal the parser paired, joined as a TOBASE line (Canada only); a `.tt` JOURNAL line's pair joined when one root or agreeing names show one security (`declared`, `_claim`, `declared_verdict`, `listing_root` — the venue suffix only —, `receipt_why` — a depositary receipt is its own root —, `declared_twins`, `partial_overlaps`, `near_restatements`; both countries; else refused `UNPROVEN` and the run stops), its record carrying `source`; a pair the user's map decides read through the map's renames (`analyze` `map_renames`: an out-leg the map books as the in-leg's other listing joins the in-leg to it; legs the map books as two symbols are a Warning, `map_split`, `map_split_note`); a broker-referenced journal's legs pair inside their account before any cross-account cancelling (`analyze` step 0b; a reference group is one journal by `src/taxjson/lib/missing_history.py` — `ref_group_journal`, the rule `own_journal_legs` and `_detected_journals` share).
- `src/taxjson/lib/symbol_codes.py` — `resolve`, `project_evidence`, `names_agree`, `is_code`, `read_state`, `codes_note`, `exact_name`, `questrade_name`, `questrade_own_name`, `code_designators`, `listing_designators`, `designators_agree`, `rbc_name`, `_CONFIRM_RE`, `_cut_account_ref`: Questrade internal codes resolved to tickers (the account's own rows first, `how` "account"; a code's warrant / right / unit / preferred / class designators read over all its descriptions must agree with the listing's); the security names (dealer confirmation wording and a transfer's account reference cut) that codes and cross-listings compare. `src/taxjson/lib/corp_actions.py` — `parse_questrade_corporate_actions` (`symbol_codes`) and `src/taxjson/bin/taxjson_corp_actions.py` — `extract_events` (`--symbol-codes`) book a spinoff chain under an internal code as the record resolves it (the event id keeps the code).
- `src/taxjson/lib/listing_suffix.py` — `scan_questrade`, `scan_rbc`, `project_evidence`, `resolve`, `fixes`, `corrections_note`, `suggestions`: a Questrade / RBC bare ticker's listing read from the books, not the row currency, a transfer's out-leg read through the map's renames (applied by `taxjson-brokerage --listing-fixes`; another account's proof for the same broker and name counts, `Evidence.proved`); a Questrade dividend reinvestment binds to the account's held listing (`src/taxjson/lib/brokerages/questrade.py` — `_rei_listing`).
- `src/taxjson/bin/taxjson_run.py` — `stage_cross_listings`, `stage_dated_events`, `_read_dated_events`, `_write_dated_events_state`, `stage_symbol_codes`, `stage_listing_suffix`, `_listing_suffix_stale`, `_ticker_map_renames`, `cmd_ticker_map`, `cmd_renames`, `cmd_journals`, `_check_renamed_late`: where the run and the commands use them.
- `src/taxjson/lib/brokerages/ib_extractor.py` — `_warn_stock_aliases`, `_book_conid_renames`, `_project_tt_renames`, `_project_map_dated`, `_project_map_file`, `_project_root`, `_project_distinct`, `_conid_seen`, `_merge_seen`, `_conid_verdict`, `_conid_link`, `_declared_clash`, `_declared_join`, `_check_declared_dates`, `_account_tt_links`, `_ib_account_prescans`, `_ib_description_tickers`, `_ib_placeholder_ticker`: IB's one contract id under two symbols booked as a rename event (event_source `ib-conid`, at the new symbol's earliest row of any section) when that contract id's own rows date it (`_ib_prescan` `conid_rows`: position rows and every dated row a Description names, OLD's last row only from rows that move a position or its cost, `_CONID_MOVES`; `ca_links`), read from every IB account of the project (`prepare_files` `project`, from `taxjson-brokerage --project-statements`, which `src/taxjson/bin/taxjson_run.py` — `stage_ib_project` writes — always, `"accounts": {}` when no other account has an IB statement, so it stays a dep of the parse), else an ATTENTION line with the `.tt` line; never on top of a corporate action the parse books as the change (a pending entry `_book_conid_renames` settles from the parsed SPLIT rows); a declared date after an account's first new-symbol trade is refused, a declaration the other way round is an ATTENTION line.
- `src/taxjson/lib/ticker_map_format.py` — `format_map`, `init_template`, `GROUPS`, `meaning`, `commented_rule`, `FormatError`, `Moved`, `_migrate_items`, `_scan_template`, `header_text`: `taxjson format-map` (ticker.map in keyword groups, comments moved with their line, the parsed map kept identical; the legacy dated events migrated: JOURNAL to TOBASE, a dated RENAME moved out as a `.tt` line; an earlier taxjson header replaced) and the ticker.map `taxjson init` writes; `src/taxjson/lib/ticker_map_legacy.py` — `LINE_HASHES`, `PARAGRAPH_HASHES`, `FINGERPRINTS`, `near_legacy`, `corpus_tables`: every header earlier taxjson versions wrote, by hash (the texts: `tests/fixtures/ticker_map/legacy_headers.txt`); `src/taxjson/bin/taxjson_run.py` — `cmd_format_map`, `_migration_notes`, `_format_map_header_notes`.
- `src/taxjson/lib/xlist_loss_radar.py` — `analyze`, `Finding`, `open_findings`, `read_state`, `message`, `suggestion_reason`, `STATE`, `_roots` (a class share's root also without its letter), `_held_at` (splits scale), `RADAR_SHOWN`, `more_message` (the console's cap), `_scoped_names`, `_source_brokers` (the names of the loss's and the purchase's own rows), `_wording_only` (names differing only in the voting-share phrase one ends with, `_voting_phrase`: a "possible" pair): a loss on one listing with another listing of the same root (an equal name) bought within 30 days — a possible superficial loss / wash sale the books cannot see until ticker.map says TOBASE or DISTINCT (work/xlist_loss_radar.state); `src/taxjson/bin/taxjson_run.py` — `_say_xlist_losses` (the run's Warning, `--strict`); `src/taxjson/lib/ticker_map_suggest.py` — `from_xlist_loss_radar` (its `TOBASE` suggestion in `taxjson ticker-map --suggest`).
- `src/taxjson/lib/ticker_map_suggest.py` — `gather`, `pending`, `Suggestion`, `from_diag`, `from_cross_listings`, `from_xlist_loss_radar`, `from_symbol_codes`, `from_listing_suffix`, `clean_extract`, `books_symbols`, `covered_by_suggestion`, `appended_text`, `verify` (the MAP-GAP pairs, certainty "verify": asked TOBASE / DISTINCT on a terminal, never added by --all): `taxjson ticker-map --suggest` and `--write` (a template line is listed, never written; a conditional hint only when the books hold every symbol it joins).
- `src/taxjson/lib/t1135_country.py` — `parse_country`, `override_value`, `NOT_FOREIGN`: the country word of a T1135 line in ticker.map.
- `src/taxjson/lib/ticker_map.py` — `similar_groups`, `similar_index`, `similar_where`, `_share_listing_spelling`: the `SIMILAR A B [C ...]` lines (look-alike funds; lines sharing a symbol merged; each member a share listing with its suffix).
- `src/taxjson/lib/similar.py` — `groups`, `booked_groups` (the members as the books spell them: a TOBASE / GLOBAL rename applied), `index`, `analyze`, `Finding`, `open_findings`, `read_state`, `message`, `checklist_note`, `caveat`, `radar_lines`, `LOSS_CATEGORIES`, `BUY_CATEGORIES`, `STATE`: a loss on one fund with a SIMILAR fund bought in the window (CA-SL-20 / US-WASH-26), from the run's work/ (the cross-listing radar's method); `src/taxjson/bin/taxjson_run.py` — `_say_similar_losses` (the run's Warning, work/similar.state), `_similar_lines` (buy-check / sell-check caveats, a sell-check caveat also from a radar row's `sheltered_buy`; `_wash_class_context` sets `canon.similar`; `_bare_listings` resolves a bare ticker to a listing a SIMILAR line names); `src/taxjson/lib/checklist.py` — `d_wash_reviewed`; `src/taxjson/lib/rebuy.py` — `_row` (`similar`); `src/taxjson/bin/taxjson_wash_radar.py` — `--similar`, `_similar_block`.
- `src/taxjson/lib/tobase_map.py` — `load_master`, `master_lines`, `render`, `parse_tobase`, `read_tobase`, `compute_overlay`, `overlay_for`, `map_text_with_overlay`, `venue_alias`, `until_findings`, `until_message`, `plan_update`, `books_changes`, `Overlay`, `GenLine`, `line_hash`, `is_edited`, `could_generate`, `retracted_listings`, `t1135_countries`, `covered_lines`, `own_map_text`, `receipt_pairs`, `receipt_pairs_for`, `DISTINCT_NOT_NEEDED`, `plan_shared`, `apply_shared`, `SharedPlan`: tobase.map, the interlisted master's pairs in a Canadian project (one file every year of a multi-year project reads, `[settings] tobase_map`; `plan_shared` / `apply_shared` make the per-year copies one for `taxjson migrate`; no DISTINCT line is written — `receipt_pairs`, the master's CDRs, keeps a CDR apart from its US share in `src/taxjson/lib/cross_listings.py` — `analyze`, `shown_apart`, `RECEIPT`, in `src/taxjson/lib/map_hygiene.py` — `pair_verdict`, `map_gaps`, in `src/taxjson/lib/ticker_map_suggest.py` — `pending`, and in `src/taxjson/lib/xlist_loss_radar.py` — `analyze`) (tax-logic CA-XLIST-06; not read in a US project, US-XLIST-05) — rendered from `src/taxjson/data/interlisted.toml` (built by `scripts/build_interlisted.py` — `build`, `merge_previous`, `check_reused`, `next_stamp`, `Figi`, `load_history`), read with ticker.map by `src/taxjson/bin/taxjson_ticker_map.py` — `_parse_map_file` (ticker.map wins; `TickerMap.generated` marks the lines it adds, which `src/taxjson/lib/map_hygiene.py` — `unused_rules` skips) and into the run's effective map (`src/taxjson/lib/cross_listings.py` — `effective_map_text`); the X.V / X.TO spelling of a TOBASE or DISTINCT line; the Warning for a row after an ended interlisting's end date; `src/taxjson/bin/taxjson_run.py` — `cmd_update_tobase_map`, `_tobase_root_year`, `_migrate_shared_tobase`, `_say_tobase_map`, `_say_tobase_until`, `cmd_init`, `cmd_new_year`; `src/taxjson/lib/checklist.py` — `s_tobase_map`, `_fp_key`, `_fingerprint_diff` (the shared file named as changed); the T1135 country of a pooled foreign issuer (`src/taxjson/bin/taxjson_t1135.py` — `build_report`); the ticker.map lines tobase.map covers (`src/taxjson/bin/taxjson_run.py` — `cmd_ticker_map`, `_tobase_covered`); sightings from the map's own lines (`src/taxjson/lib/map_hygiene.py` — `own_renames`).

## Corporate actions and elections

Mergers, spin-offs, tenders and reorganizations are read from each broker's
corporate-action rows into `CorporateAction` events. Each event needs an
election (taxable or a rollover, by country); the choice is saved in a
manifest and turned into transaction rows. Undecided events stop the run with
an election list (a sheltered account's spin-off or merger is booked without
asking unless `sheltered_elections = "ask"`); `taxjson elect` records the
choices. Splits and renames use
one shared timeline so their arithmetic is the same everywhere.

- `src/taxjson/lib/corp_actions.py` — `CorporateAction`, `parse_ib_corporate_actions`, `parse_questrade_corporate_actions`, `parse_rbc_corporate_actions`, `RULES_BY_COUNTRY`, `RuleSpec`, `resolve_event`, `options_for`, `apply_auto_defaults`, `IGNORE_ELECTION`, `Manifest`, `ElectionRecord`, `_ib_union` (an IB account's statements' rows read together, a row repeated with another value kept once), `combine_broker_copies` (`_same_listing`, `_value_differences`, `_larger_copy`: which copies are one holding, what they disagree on, which is kept): the broker extractors; the election rules per country; the saved elections (the manifest) and dedup of one event seen by two brokers.
- `src/taxjson/bin/taxjson_corp_actions.py` — `main`, `EXTRACTORS`, `extract_events`, `_prompt_election`, `_pending_doc`, `_emit_resolved`, `EXIT_ELECTIONS_REQUIRED`: the `taxjson-corp-actions` stage (`--sheltered-elections`: a sheltered account's events booked without asking).
- `src/taxjson/lib/corp_actions.py` — `_allocated_spinoff_selling_fraction`, `CIL_TAKEOVER_TYPE`, `drop_broker_cil_pairs`, `warn_unsold_spinoff_cil` (`read_tt_sales`, `_tt_fraction_sale`: a fraction sold by hand in the account's `.tt` files, passed by the run with `--tt`), `_qt_spinoff_cil`, `rbc_spinoff_cil_fraction`: an s.86.1 spin-off's cash in lieu of a fraction sold at the cash (CA-CORP-05): the Questrade and RBC extractors attach the CIL row to the spin-off event, the rollover books the sale, and `src/taxjson/bin/taxjson_merge2.py` — `drop_broker_cil_pairs` drops Questrade's own $0 pair for it.
- `src/taxjson/lib/corp_actions.py` — `SHELTERED_DEFAULT`, `sheltered_elections_mode`, `sheltered_default_applies`, `sheltered_default_rows`, `sheltered_default_text`: a sheltered account's spin-off ($0 cost) or merger (cost carried) booked without asking, `[settings] sheltered_elections`.
- `src/taxjson/bin/taxjson_run.py` — `cmd_elect`, `_print_pending`, `_print_elections`, `_warn_zero_value_spinoffs`, `_note_sheltered_defaults`, `_sheltered_elections`, `_country_has_corp_rules`: `taxjson elect` and the run's election messages.
- `src/taxjson/lib/corporate_timeline.py` — `SplitTimeline`, `cumulative_factor`, `split_event_key`, `split_seen`, `split_copies` (the one duplicate-split rule every walk of split events uses, dict rows and engine rows alike), `event_sort_key`, `account_tie`, `radar_priority`: split and rename arithmetic and same-day event order (rows of two accounts at one moment in account-name order, CA-DATE-14 / US-DATE-13; the engines also take their input books in that order, `src/taxjson/lib/core.py` — `_by_account_name`).
- `src/taxjson/lib/corp_views.py` — `spinoffs`, `splits`, `render_spinoffs`, `render_splits`, `wrong_country_elections`, `_held`, `_rows`: `taxjson spinoffs` and `taxjson splits` (`_rows`: the books through `json_input.read_work_doc`, a damaged one refused); `_held` replays the shares held through `balance_on` (`src/taxjson/bin/taxjson_apply_distributions.py`), so a split is applied at the open like the balance walk does; a saved `fmv_per_share=0` beside a broker-reported value is explained (the broker's value is booked), never flagged.

## Currency conversion and FX rates

Every amount is converted to the home currency (CAD or USD) at the day's
rate. `stage_currency_rates` builds the project's rates file by calling
`taxjson-to-base-curr` per source currency (Bank of Canada, with fallbacks);
the rates are cached in the home folder.

- `src/taxjson/bin/taxjson_run.py` — `stage_currency_rates`, `_rates_coverage_stale`, `_raw_align_adjust_currency`, `_home_currency`: the run's rate file, its freshness, and ADJUST rows (and a purchase whose `listing_currency` is not its cash's, a Questrade REI) restated in the pool's currency for the raw holdings books.
- `src/taxjson/bin/taxjson_run.py` — `_rates_inputs`, `RATES_START_STAMP`; `src/taxjson/lib/rates_window.py` — `window_start`, `earliest_in_text`, `PAD_DAYS`: the first date the rates must cover, from the earliest date written in the project's exports, `.tt` files, slips and positions snapshots (never later than January 1 of the year), recorded beside the rates file.
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

- `src/taxjson/bin/taxjson_apply_distributions.py` — `apply_distributions`, `main`, `balance_on`, `resolve_live_symbol`, `_warn_roc_overlaps`, `_snapshot_after`, `_snapshot_on`: the `taxjson-apply-distributions` stage; `_stamp_date`, `_stamp_problem`: the ADJUST's trade date in a trade-date-ordered engine (backdated before a sale straddling the record date; the row's `record_date` lets the US engine's `dist` ADJUST branch in `src/taxjson/lib/core.py` take the lots settled by it only).
- `src/taxjson/lib/project_tables.py` — `distribution_rows`, `claimed_losses`, `table_problems`, `DIST_TABLE`, `CGD_TABLE`: the hand-entered year tables in taxjson.toml.
- `src/taxjson/lib/cg_dividends.py` — `parse_map`, `entries_from_config`, `allocate`, `CgDividendMapError`: T5 box 18 capital-gains dividends.
- `src/taxjson/lib/income_dating.py` — `rules_for`, `IncomeRules`, `parse_ric_entries`, `is_canadian_issuer`, `split_share_roots`: income-year and payment-in-lieu rules; `is_return_of_capital`: which ADJUST rows are a return of capital (slip-audit's T3 box 42 and the roc-sum total).
- `src/taxjson/lib/pipeline.py` — `apply_roc_record_dates`, `apply_trust_roc_record_dates`, `income_dating_flags`, `add_income_dating_args`: how the gains run applies them.

## Transfers, crypto sends and opening balances

TRANSFER rows are custody evidence, kept out of the books unless something
decides otherwise. Shares that arrive in a taxable account from outside the
books are booked at the broker's stated book value, or flagged. In a US
project a move between two of your own taxable accounts carries its lots. A
crypto send that never arrives in another of your accounts may be a
disposition, decided once and saved. Opening balances come from a broker's
positions report.

- `src/taxjson/lib/transfer_in.py` — `Arrival`, `arrivals`, `OWN_MOVE_DAYS`, `SLOW_MOVE_DAYS`, `slow_pairs`, `SlowMove`, `slow_dates`, `LISTING_JOURNAL_DAYS`, `sidecar_rows`, `own_journal_legs`, `movable_rows`, `stated_book_value`, `booked_rows`, `mark_covered`, `_tt_events`, `_tt_units_at`, `attention_lines`: shares that arrived by transfer; a journal inside one account settled within its own pair; an out leg cancels an in leg of the same security within 10 days, a listing journal two listings of one root, then a slower move of your own (`slow_pairs`: the in leg 11 to 45 days after an out leg of the same quantity in another account, neither with another candidate; `Arrival.departures` the earlier out legs nothing paired, named on the ATTENTION line; the same rule in the US custody pairing, `src/taxjson/bin/taxjson_run.py` — `_custody_pairing`, and the purchase drafts, `src/taxjson/lib/missing_history.py` — `_draft_transfers`); the .tt coverage replay (splits, sales before the arrival, partial coverage said and not booked).
- `src/taxjson/lib/in_kind.py` — `Leg`, `Move`, `legs`, `pair_all`, `Pairing`, `pair`, `_parts`, `line_legs`, `in_parts`, `parts_message`, `apply_lines`, `decide`, `contract_size`, `value`, `booked_rows`, `mark_sheltered`, `message`, `contribution_results`: in-kind contributions and withdrawals between a taxable and a registered account (CA-INKIND-* / US-INKIND-*); `_parts` finds a declared move's legs delivered in parts (every candidate counts towards uniqueness, within `_PARTS_STEPS`), `contract_size` the option contract size a move is valued and priced at (the row's `multiplier`, else lib/core's rule).
- `src/taxjson/bin/taxjson_run.py` — `in_kind_state`, `stage_in_kind_context`, `_say_in_kind`, `in_kind_taxable_legs`, `in_kind_leg_idents`, `_registered_transfer_rows`, `_inkind_lines`, `_in_kind_close`, `in_kind_unbooked`, `in_kind_fix_line`: the run's in-kind moves (work/in_kind.json), the plan's purchase marked in work/sheltered_base.json, the one warning; the moves NOT booked, read back by `sum` and the checklist's transfers step.
- `src/taxjson/bin/taxjson_convert_tt.py` — `parse_inkind_line`: the `.tt` INKIND line (a value, never a row of the books).
- `src/taxjson/lib/transfer_in.py` — `NotOwn`, `parse_not_own_line`, `read_not_own`, `not_own_text`, `not_own_account_problem`, `leg_ident`; `src/taxjson/bin/taxjson_run.py` — `not_own_legs`: the `.tt` NOTOWN line, which undoes one own-move pairing (`arrivals`' exact or slow pair; the US custody pairing leaves its legs alone) and never makes one (backlog TJ-0075).
- `src/taxjson/bin/taxjson_run.py` — `stage_transfer_arrivals`, `transfer_arrivals`, `stage_own_account_moves`, `own_account_custody_moves`, `_stage_crypto_sends`, `cmd_crypto_sends`, `cmd_opening`, `_opening_lines`, `_opening_iso_day`, `_opening_padded_day`: where the run books arrivals and own-account moves; the crypto sends hook and `taxjson crypto-sends`; `taxjson opening`, which writes OPENING lines from a positions report (its date checked with the `.tt` reader's YYYY-MM-DD rule, each line read back before the file is written).
- `src/taxjson/lib/transfer_in.py` — `OwnMove`; `src/taxjson/bin/taxjson_run.py` — `_stage_pooled_own_moves`, `_move_legs`; `src/taxjson/lib/core.py` — `_own_move_view`, `_own_move_units`, `LOT_MOVE_TYPE`; `src/taxjson/lib/corporate_timeline.py` — `_walk_rest`: Canada's moves of your own between two taxable accounts (CA-ACB-OWN-MOVE, backlog TJ-0093): the transfer-in pairing names the units it cancelled between two accounts, the run's transfer-arrivals stage writes them as lot-move legs to work/<account>_own_moves.json, and the Canadian engine moves the units in one account's own books only (the blended pass drops the legs: one ACB pool); the history walks read an in leg at the start of its day.
- `src/taxjson/lib/crypto_sends.py` — `load_transfer_rows`, `match_transfers`, `_assign` (the send/arrival pairing: a lexicographic assignment — most pairs, fewest coins lost, closest in time), `build_report`, `record_decision`, `clear_decision`, `_manifest_lock` (a decision's load, change and save under one lock), `prompt_undecided`, `render_tt`, `DECISIONS`, `account_pool_flows`, `merge_pool_flows` (the stablecoin pool's flows: each account's overlapping exports read once, the accounts added up): crypto sends, their decisions and the generated crypto_sends.tt.
- `src/taxjson/lib/crypto_sends.py` — `fair_value`, `tt_line`, `keep_written_lines`: a send's value at the full-precision price; a line written before kept.
- `src/taxjson/lib/opening.py` — `apply_opening_cutoff`, `snapshots`, `OpeningError`, `ATTENTION_OPENING`, `_realizations_left_out`, `_nil_lot_held`, `_refusal`: OPENING rows and the cutoff they impose on earlier rows (a left-out sale, short cover or return of capital that may go beyond the cost, of the tax year, stops the run).
- `src/taxjson/lib/pipeline.py` — `_handle_transfers`, `_net_cross_account_transfers`, `TransferValidationError`, `transfers_in_loss_windows`, `transfers_as_acquisitions`: transfer handling before the engine runs.

## Missing purchase history

When the files do not reach back to a purchase, a sale has no cost. These
sales are detected, listed with suggestions, and can be answered with a
dated `.tt` line `OPENING <date> <SYMBOL> <qty> cost=unknown` (an opening
lot the gains run synthesizes; the project-root missing_history.json of
older versions is refused and `taxjson migrate` converts it) or a draft of
the missing purchases.

- `src/taxjson/lib/missing_history.py` — `detect_missing_history`, `MissingHistoryCandidate`, `classify_year_shorts`, `missing_history_suspects`, `detect_zero_basis_acquisitions`, `load_missing_history`, `synthesize_openings` (a line opens its quantity on its date; a pair without one — a suggestion, an old entry being converted — is sized from the rows through the tax year's end: `sizing_until`; `short_again_message`), `stale_missing_history_entries`, `MISSING_HISTORY_FILE`, `draft_purchases`, `detect_superficial_loss_warnings`, `_walk_key`, `walk_journal_symbols`, `detected_journal_symbols`, `journal_leg_key`, `journal_targets`, `_journal_line_symbols`, `JournalDays`, `_journal_trade_day`, `_opposite_trades`, `books_journal_days`, `undeclared_journal_days`, `read_tt_openings`, `TtOpening`, `TtOpeningError`, `MISSING_HISTORY_TT`, `missing_history_arg`, `project_missing_history`, `has_tt_openings`, `_apply_fixed_openings`, `_symbol_on`, `openings_from_log`, `plan_tt_write`, `apply_tt_plan`, `default_opening_date`, `plan_missing_history_migration`, `project_view`, `apply_missing_history_migration`, `read_legacy_entries`, `legacy_missing_history_file`, `MissingHistoryNoLongerRead`: finding sales with no purchase; the `.tt` OPENING cost=unknown lines (read through the project's missing-history path, applied with their date and quantity; written by find-missing-history and migrate); the project file and the openings synthesized from it; purchase drafts and loss-window warnings; the walks' order, where a journal between a security's two lines (a join of the run, a Questrade `journal_pair`, RBC's J~ reference on an RBC export, a `.tt` JOURNAL line, a legacy JOURNAL line) reads a day's buys and in-legs first on its days only (its legs' days and its own trades' day — its quantity, FROM bought and TO sold; a TOBASE line names none, and the run names the `.tt` line for a day that looks like one).
- `src/taxjson/bin/taxjson_missing_history.py` — `main`, `_print_section`, `_opening_sizes`, `_print_zero_section`, `_write_purchases`: the `taxjson-missing-history` tool.
- `src/taxjson/bin/taxjson_run.py` — `cmd_find_missing_history`, `_write_missing_history_tt`, `_missing_history_arg`, `_migrate_missing_history`, `_missing_history_suspects`, `_year_short_rows`, `_refuse_unusable_missing_history`, `_refuse_legacy_project_files`, `_refuse_stale_books_write` (no write flag on books older than the inputs): `taxjson find-missing-history`, `taxjson migrate`'s conversion and the run's use of the lines.
- `src/taxjson/lib/migrate.py` — `MISSING_HISTORY_FILES`, `legacy_files`, `legacy_message`: the old missing_history.json (and phantoms.json) refused like the old per-purpose maps.
- `src/taxjson/bin/taxjson_convert_tt.py` — `parse_unknown_opening_line`, `unknown_opening_text`, `UNKNOWN_OPENING_FORM`: the `.tt` line's form.
- `src/taxjson/lib/phantom_holdings.py` — `missing_history`: the old module name, kept as an alias.
- `src/taxjson/lib/option_close_check.py` — `unbacked_option_closes`, `unbacked_option_close_messages`, `_clocked` (a same-day opening backs a close only while the day's order is unknown: no clock on a row, or one stamp): option rows the broker marks closing that the books cannot back.

## The gains engines

`core.py` holds the transaction model and both engines. The Canadian engine
pools shares at adjusted cost base per security across taxable accounts and
applies the superficial-loss rule (with sheltered and affiliated accounts in
view). The US engine (experimental) keeps FIFO lots, per account when asked,
and applies the wash-sale rule. Both handle splits, renames, option exercise
and assignment, and option replacement.

- `src/taxjson/lib/core.py` — `TaxTransaction`, `load_transactions`, `coerce_transaction_row`, `canonical_date`, `require_computable_rows`, `SYMBOL_REQUIRED_ACTIONS`, `carry_row_marks`, `TaxRules`, `CanadaTaxRules`, `USATaxRules`, `get_tax_rules`, `compute_gains`, `find_replacements_in_window`, `make_gain_entry`, `detect_option_replacement_matches`, `_AssignPremiumLedger`, `disposition_groups`, `is_option_symbol`, `held_more_than_one_year`, `not_a_purchase`, `IN_KIND_CONTRIBUTION_TYPE`: the transaction model and loading (every JSON row goes through `coerce_transaction_row`: types, finite numbers, dates written YYYY-MM-DD); the computation's input contract (`require_computable_rows`, called by both engines' `compute_gains` and by `prepare_books`: a trade without its amount, an empty date or symbol, an action the engines do not book); the two engines (search `class CanadaTaxRules`, then its `compute_gains`); the US lot and wash-sale machinery; option replacement checks, assignment premiums, disposition grouping and option helpers.
- `src/taxjson/lib/core.py` — `_rename_ratio_groups`, `_multi_ratio_rename_groups`, `_fold_per_account_rename_ratios`, `_per_account_rename_ratios`, `_split_covers`, `pool_qty_eps`, `is_stock_dividend`, `_ent_date`: a merger delivered at different whole-share ratios per account (Canada: folded into one event at the holdings-weighted ratio, CA-CORP-13; US with per-account lots: each row scales and migrates its own account's lots, US-CORP-14; US symbol-wide lots: folded); the flat tolerance of a pool (shares 1e-6, a coin only float noise) that the ADJUST landing and `pending_wash` recovery share with the trades; the §307 stock-dividend spread over the lots entitled to it (bought before the ex / record date).
- `src/taxjson/lib/core.py` — `_DatedRows`, `_ClassBalances`, `_WashAdjustIndex`, `_iso_ordinal`, `_class_balances`, `_dis_by_id`, `_sl_trg`, `_sl_call`, `_sl_sym`: the Canadian superficial-loss scans' indexes (each identity class's purchases and calls by date window, its day-30 balances as running sums per holder, the solver's denials and ADJUSTs by loss id); each hands a scan the rows the walk of the whole book kept, in its order, so a run grows with its rows, not loss sales x rows (TJ-0105).
- `src/taxjson/lib/numeric.py` — `D`, `round_half_up`, `round_floats`: decimal arithmetic for money.
- `src/taxjson/lib/loss_overrides.py` — `parse_line`, `read_project`, `write_state`, `read_state`, `flags`, `plan`, `summarize`, `stamp_entries`, `gather`, `problems`, `spelling_hint` (a no-match line: how the books spell the sale — another listing of the root, a ticker.map spelling, a date a few days off), `map_view`, `describe`, `warning_message`, `positions`, `STATE`: filing positions against the loss rule (`.tt` ALLOWLOSS lines, CA-SL-18 / US-WASH-25): the line, the run's `work/loss_overrides.json` and the `--loss-overrides` flag every engine run of the books gets (`src/taxjson/lib/pipeline.py` — `add_loss_override_args`, `loss_overrides_from_args`, `GainsRequest`), the sale each line names (`plan`, in the engine's order), what the rule would have denied (the engines record it, `loss_overrides` in a gains file, `loss_override` on the sale's rows), and the views: the run's Warning and its stop (`src/taxjson/bin/taxjson_run.py` — `_read_loss_overrides`, `_loss_override_flags`, `_say_loss_overrides`), `sum` and `wash-sales` (FILING POSITIONS), the checklist (`src/taxjson/lib/checklist.py` — `d_filing_positions`); the per-row note (`note_text`) on the return forms (`src/taxjson/bin/taxjson_form_export.py` — `_filing_position`, `_merge_positions`), in `audit` (`src/taxjson/bin/taxjson_audit.py` — `build_event`) and the explain traces (`src/taxjson/lib/trace_format.py` — `filing_position_text`), the US radar (`src/taxjson/bin/taxjson_wash_radar.py` — `_us_engine_losses`), `taxjson carryover` (`src/taxjson/bin/taxjson_carryover.py` — `filing_positions`) and `taxjson handoff` (`src/taxjson/bin/taxjson_run.py` — `_handoff_positions`).
- `src/taxjson/lib/wash_scope.py` — `scope_note`, `scope_lines`, `advisory_lines`, `scope_short`, `scope_more`: what the planning verdicts can and cannot see, per country.

## The gains run and the wash passes

`pipeline.py` is the one definition of a gains run: load-side preparation
(transfers, missing-history openings), the country's engine options (option
premium timing, US per-account lots), then the engine. `taxjson-gains` is its
command line. After each account has gains, a second pass re-runs them with
the other accounts in view (cross-account superficial loss); in Canada the
taxable accounts run as one blended pass and are split back per account.

- `src/taxjson/lib/pipeline.py` — `run_gains`, `GainsRequest`, `prepare_books`, `engine_options`, `option_timing_from_settings`, `option_timing_flags`, `place_retro_wash_adjustments`, `annotate_inventory_multipliers`, `declared_multipliers`, `tt_json_path`: one gains run; US retroactive wash adjustments and contract multipliers.
- `src/taxjson/bin/taxjson_gains.py` — `main`, `_request`, `_suggest_missing_history_and_exit`, `write_traces_file`, `_since_shown` (a block of the traces file shows the pool lines since the block above of the same pool): the `taxjson-gains` tool. A sale's trace is a `src/taxjson/lib/trace_format.py` — `SharedTrace`: a view of its pool's running trace in `src/taxjson/lib/core.py` (`symbol_acb_traces`), read as the whole history by `taxjson audit` and taxjson-explain, never copied per sale.
- `src/taxjson/bin/taxjson_split_gains.py` — `split_for_account`, `main`: splits a blended gains run back into per-account files.
- `src/taxjson/bin/taxjson_run.py` — `stage_wash_pass`, `stage_blended_wash_pass`, `_blend_conservation_gaps`, `_wash_flags`, `_render_wash_outputs`, `_record_wash_inputs`: the wash passes in a run, and the record of what each wash file was built from (`work/.wash_inputs.json`, for the stale check).

## Per-account reports

Each account ends with a `reports/<name>.sum` text report and its JSON twin:
gains, income, option summaries and holdings. Cross-account reports (covered
calls, long options, the wash radar, cross-listing lint) and the fee report
come after.

- `src/taxjson/bin/taxjson_sum_gains.py` — `summarize_gains`, `format_report`, `load_gains_data`, `main`: the gains section of a `.sum`.
- `src/taxjson/bin/taxjson_sum_income.py` — `summarize_income`, `format_report`, `load_income_data`, `_checked_amount`, `main`: the income section.
- `src/taxjson/bin/_option_gains_report.py` — `process_data`, `load_inputs`, `main`: shared body of the two option reports.
- `src/taxjson/bin/taxjson_ccd_gains.py` — `process_data`, `main`: short calls (covered calls) by underlying.
- `src/taxjson/bin/taxjson_leaps_gains.py` — `process_data`, `main`: long options by underlying.
- `src/taxjson/lib/listing_journals.py` — `book_journals`, `evidenced_journals`, `identity_find`; `src/taxjson/bin/taxjson_run.py` — `_raw_book_journals`, `_account_sidecars`, `_raw_rate_lookup`; `src/taxjson/lib/core.py` — `LISTING_JOURNAL_TYPE`, `_journal_legs`, `_journal_factor`, `_journal_units`, `_book_journal`, `_carry_lots`; `src/taxjson/lib/pipeline.py` — `_handle_transfers`: a journal between two listings in the native-currency books behind the holdings export (backlog TJ-0361, TJ-0363): the run pairs the account's own transfer legs and its transfer evidence within the map's identity classes and writes two listing-journal TRANSFER rows into work/<account>_raw.json on the journal's date; both engines move the units with their cost (a short the other way), restated in the new listing's currency by the in-leg's rate; the transfer handling sets the legs aside, and the run passes the export no evidence.
- `src/taxjson/bin/taxjson_export.py` — `render_report`, `render_holdings_toml` (`--crypto`: a crypto account's own books), `process_data_report`, `contract_multiplier_of` (an option's declared size; unknown when the inputs disagree), `_holdings_toml_to_inventory` (a holdings TOML read back, mixed-currency buckets included), `_load_trade_events` (each holding's current-position `trades`), `_evidence_rows`, `_evidenced_journals`, `_apply_transfer_evidence`, `_replay_moves_on_base` (the transfer sidecars' journals between listings, applied to the holdings), `main`: holdings as a text report or a TOML snapshot.
- `src/taxjson/bin/taxjson_fees.py` — `aggregate`, `metrics`, `render_text`, `render_json`, `main`: the fee report by broker (`taxjson-fees-sum`).
- `src/taxjson/lib/staking_report.py` — `build_report`, `render`: reward evidence, commissions, recorded income and acquisition cost, grouped by exchange and coin with unknown-evidence coverage; `src/taxjson/bin/taxjson_run.py` — `_staking_rows`, `_staking_view`, `cmd_staking`, `cmd_staking_sum`: period/account/exchange/coin selection from the final base-currency books and the two command views.
- `src/taxjson/lib/report_model.py` — `build_account_report`, `resolve_gains_files`, `stale_wash_inputs`, `_STALE_SAID` (each stale-wash warning once per process), `record_wash_inputs`, `_book_accounts` (the roster of accounts a pass saw: a new one makes it stale), `render_table`, `align_columns`, `fmt_money`: shared report pieces and the account report JSON; which gains file a report reads and whether the wash-adjusted one is stale (its own books, and every member of its cross-account pass, from `work/.wash_inputs.json`).

## Output style and messages

All human output follows docs/output-style.md: wrapped prose, labelled
messages (Warning, Note, Error, ATTENTION), aligned tables. Stage messages are
captured during a run and re-worded for the console.

- `src/taxjson/lib/out.py` — `wrap`, `message`, `warn`, `note`, `attention`, `error`, `fail`, `fit_table`, `Doc`, `lint`, `console_lint`, `WIDTH`, `MAX_WIDTH`, `width`, `show`, `show_blocks`, `join_blocks`, `settle`, `settling_streams`: the house style; the style checks tests use; the wrap width (`TAXJSON_WIDTH`; 120 piped, the terminal's up to 160); a message printed with its lines flush-left and the one blank line a multi-line message owes its destination.
- `src/taxjson/lib/stage_msg.py` — `emit_line`, `say`, `reword`, `console_lines`, `is_continuation`, `split_message`: stage messages and the run console.
- `src/taxjson/lib/out.py` — `act`, `act_lines`, `act_list`, `classify`, `prose_lines`, `details_hint`, `concise_on`, `concise_show`, `truncate`, `fold_summary`, `FOLD_SHOWN`, `CONCISE_ENV`: Essentials first — the `! ` lines to act on (`act`, `act_list`), the classification of a default view's lines (legend, table, act-on, prose) the style tests check (`classify`), the pointer to `--details`, a message cut to one line and the fold of a message said more than twice.
- `src/taxjson/lib/stage_msg.py` — `concise_line`, `detail_command`, `shorten_commands`, `_CONCISE`, `env_concise`: a stage message as one console line (its headline and the command its detail names; `taxjson X` spelled `tjs X` when it would not fit; the frequent wordy ATTENTION lines in a fixed short form).
- `src/taxjson/bin/taxjson_run.py` — `_details`, `_CURRENT_DETAILS`, `_brief`, `_brief_messages`, `_DETAILS_CMDS`, `_OWN_MESSAGES`: every command's `--details`; the shared report warnings read `_CURRENT_DETAILS`; `_brief_messages` folds the messages an engine prints while a command recomputes.
- `src/taxjson/lib/cli_diag.py` — `warn`, `error`, `note`, `read_text_utf8`, `write_text_atomic`, `InputReadError`: stderr diagnostics and safe reads and writes for tools.
- `src/taxjson/lib/trace_format.py` — `render_gain_block`, `render_report_trace`, `render_summary_table`, `render_document_header`: the per-gain trace boxes.
- `src/taxjson/lib/install_hint.py` — `extra_hint`, `INSTALLER`, `NOT_ON_PYPI`: the install lines the CLI prints.
- `docs/output-style.md` — `Essentials first`, `Width`, `Messages`, `The run's console`, `Numbers, dates`: the style rules.

## Row listings

The listing commands read the native (before base-currency) per-account files
and print rows in .tt style, filtered by period, account, symbol and action.

- `src/taxjson/bin/taxjson_run.py` — `_run_tx_view`, `cmd_transactions`, `cmd_dividends`, `cmd_dil`, `cmd_buysell`, `cmd_roc`, `cmd_gains`, `cmd_leaps`, `cmd_fees`, `cmd_transfers_view`, `cmd_cash`, `_view_window`, `_period_keep`, `_period_end`, `_tx_period_cutoff`, `_require_books`: `events`, `divs`, `dil`, `trades`, `roc`, `gains`, `leaps`, `fees`, `transfers` (the TRANSFER evidence; a transfer-in is `own move` only when `transfer_arrivals`' `paired` names it, `UNCLASSIFIED` when the classification fails) and `cash` (the cash events as `.tt` lines: `src/taxjson/lib/cash_events.py` — `tt_line`, `tt_comment`, `UNDECLARED`); the shared period filter (a look-back or to-date window ends today: `_period_end`) and the "run first" check.

## Totals and positions

The `*-sum` commands total one row type per security or per year. `list` and
`shares` show positions from the canonical books (after ticker.map and
base-currency conversion, wash-adjusted where built).

- `src/taxjson/bin/taxjson_run.py` — `cmd_divs_sum`, `cmd_dil_sum`, `cmd_roc_sum` (`_roc_sum_other`, `_adjust_kind`: the ADJUST rows that are no return of capital), `cmd_trades_sum`, `cmd_fees_sum`, `cmd_ccd_sum`, `cmd_leaps_sum`, `cmd_winners`, `cmd_losers`, `_ranked_view` (winners and losers, one side each), `cmd_positions` (`_list_positional_date`: `list [ACCOUNT] [YYYY-MM-DD]`), `cmd_shares`, `_box18_fractions`: totals by type; option totals and the ranked winners and losers; `list` and `shares`; `_window_gains_files`, `_gains_all_file`, `_gains_artifact_scope`, `_warn_gains_artifact_scope`, `_leaps_scope_guard`, `_leaps_closed`: the gains files a windowed view (`stats`, `win-rates`, `winners`, `losers`, `ccd-sum`, `leaps`, `leaps-sum`) reads — the tax year's, or for a window outside it the full-history `work/<account>_gains_all.json` built on demand from `<account>_base.json`.

## Summaries and estimates

`sum` prints the year's summary across accounts (gains, income, by form
line). `estimate` and `amt` estimate the tax and the Canadian minimum tax;
`instalments` schedules Canadian instalments; `plot-tax` charts the tax each
month's income generated against the estimate; `fx-cash` computes FX gains on
foreign cash; `stats` is a trader's win and loss view per asset class and
`win-rates` the same trades per ticker, account, month or class.

- `src/taxjson/bin/taxjson_run.py` — `cmd_summary`, `_report_aggregate_usable` (the report file's aggregate is served only when complete), `_section_1256_gain`, `cmd_estimate`, `cmd_amt`, `_tax_estimate_result`, `_print_tax_estimate`, `_losses_carried_back` (the T1A carry-back the estimate prints and its `--json` carries), `_rounding_legend` (`sum`'s line on SUBTOTAL vs RETURN), `cmd_instalments`, `_instalment_config`, `cmd_fx_cash`, `cmd_stats`, `_closed_trade_inputs`, `_option_openings` (a missing-history option opening the option views add, as the gains stage does), `cmd_win_rates`: `sum`, `estimate`, `amt`, `instalments`, `fx-cash`, `stats` and `win-rates`.
- `src/taxjson/lib/readiness.py` — `build`, `conservation_lines`, `CONSERVATION_PREFIX`: whether a filing figure is complete, the readiness record of `sum --json` and `form-export --json` (`src/taxjson/bin/taxjson_form_export.py` — `--run-state-problem`).
- `src/taxjson/lib/tax_estimate.py` — `estimate_canada`, `estimate_usa`, `_amt_canada`, `ca_amt_carryover`, `apply_vintage`, `CA_FED_BRACKETS`: brackets, credits and the AMT, by rate year.
- `src/taxjson/lib/tax_plot.py` — `build`, `chart`, `render`, `slice_doc`, `months_of_year`, `HEIGHT`: `taxjson plot-tax`, the monthly tax chart; `src/taxjson/bin/taxjson_run.py` — `cmd_plot_tax`, `_fold_estimate_income` (the estimator's inputs from one account's aggregates, shared with `sum` / `estimate`), `_withholding_rows`, `_withholding_total`, `_actual_withholding` (the foreign tax withheld the estimate credits: the year's, or a month's through its end).
- `src/taxjson/lib/amt_report.py` — `build`, `render`, `render_recorded`: the AMT page.
- `src/taxjson/bin/taxjson_instalments.py` — `build`, `render`, `render_brief`, `required_schedule`, `interest_and_penalty`, `PUBLISHED_RATES`: instalment schedule, interest and penalty.
- `src/taxjson/bin/taxjson_fx_cash.py` — `build_ledger`, `apply_jurisdiction`, `render_report`, `render_brief`, `unreliable_status`, `CA_EXEMPTION`: FX gains on foreign-currency cash, the default ledger (v1: trades and income only, flagged NOT RELIABLE everywhere).
- `src/taxjson/lib/trade_stats.py` — `closed_trades`, `economic_pnl`, `compute`, `group`, `classify`, `written_option_trades`, `credits_by_sale`, `_assignment_legs`, `CLASSES`, `GROUPINGS`: the closed trades, their statistics per class (`stats`) and per ticker, account, month or class (`win-rates`); `economic_pnl` is a gains row's P/L before any denial (its `raw_gain` plus the earlier denied loss its cost carries, the engines' `deferred_loss_recovered` in `src/taxjson/lib/core.py`).
- `src/taxjson/lib/fx_cash_v2.py` — `build`, `_waits` (on one settlement day, an own move's arrival walked with its departure), `_carry_openings`, `_carried_book`, `_book_account` (the prior year's close checked, never trusted, and each carried book placed by its broker account), `headline`, `render`, `TOL`, `LABEL`: the opt-in ledger v2 (debt per broker account, reconciliation to statement balances, refusals); `src/taxjson/lib/cash_events.py` — `collect`, `parse_line`, `opening_problem` (the `CASHOPEN` rule, which a carried book is held to too), `read_project_lines`, `pair_internal`, `apply_declarations` (a .tt CASHMOVE line declaring a broker's move; the ledger and `taxjson cash` share it), `Books`, `book_name`, `_short_width`, `FORMS`: the cash-event book it reads (the `.tt` lines `FXCONV`, `CASHMOVE`, `CASHOPEN`, `CASHBAL`, `CASHBOOK` (`complete`), and each broker's `ib_cash_events`, `rbc_cash_events`, `kraken_cash_events`, `coinbase_cash_events`, `questrade_cash_events` in `src/taxjson/lib/brokerages/questrade.py`; `READERS` names them, and `Books.unread` the books of a broker with none, which v2 refuses until declared complete); `src/taxjson/bin/taxjson_run.py` — `_fx_cash_status`, `_fx_cash_v2_doc`, `_fx_cash_ledger_choice`, `_fx_cash_carry`, `_fx_sum_item`: which ledger, the status `sum` / `checklist` / the run hook show, and the prior year's close (`fx_cash_v2` in the close-year record).

## Checks before filing

The checklist (`taxjson checklist`) is every step from install to filing
in ten sections, each step proved by a command or marked from the
project's files; outside a project it prints the steps as a guide.
`--run` runs the checks (the slow ones `-j N` at a time, threads over
sub-commands: `src/taxjson/lib/checklist.py` — `_run_detectors`,
`default_jobs`, `SLOW`) and keeps the verdicts in
`work/checklist_results.json` (`RESULTS_FILE`, `save_results`); without
`--run` they are shown as kept (`load_results`, `RunInfo`,
`run_info_lines`: when they were made, what moved since — each verdict
judged by its own basis (`_bases`, `_basis_now`, `_stale_reasons`: the
fingerprint of the run inputs and of the files only the checks read,
`checklist_fingerprint`, `_check_only_files`; the books' build time;
the version), the store read, merged and written under `_StateLock`,
another year's verdicts dropped and the shape's n/a never stored
(`_SHAPE_NA_DETAILS`); `NOT_CHECKED` for a
step never checked; `has_books`: a work/ holding only that file is no
work/ to the detectors). `sanity` compares the books against broker positions reports;
`check-dates` checks every trade time against market hours; `edge-cases`
lists rows whose treatment turns on a boundary.

- `src/taxjson/lib/checklist.py` — `_detail_cmd`, `_inputs_label`, `_do` (the closing line: a blocked step's own remedy, the exports folder as the project sees it; a `! ` line's command is the result's `cmd`, else the one its detail names, else the step's first), `STEPS`, `US_STEPS`, `DETECTORS`, `evaluate`, `render` (the default view: the list — every step, an open step's commands, the next step's how and why; `LONG_FORM` and `--all` every step's — then `_summary_block`: the counts per section, one `!` line per step needing attention; `_title` names a `--section` / `--only` scope), `render_summary` (the summary alone, after a `--walk`), `resolve_id` (a step by its id or its number: `--done 18`), `resolve_section`, `section_ids`, `section_label` (`--section 4`, `--section fill`: one section shown, checked or walked), `once_progress`, `ACT_WIDTH`, `Ctx`, `Result`, `input_fingerprint`, `record_input_fingerprint`, `inputs_changed`, `LOCKED_YEARS_KEY` (a US project's filed-year locks, which its gains read), `load_state`, `set_override`, `_SAVED_ID_MIGRATION` (a mark saved in checklist.json under the tips item's former id is read as the tips item's), `d_journals`, `d_renames`: the checks, one detector per check; the git checks (`d_inputs_committed`, `d_lock_committed`) go through `_git` (hooks and fsmonitor off) and `_git_status`, and are blocked by `_git_refusal` when the repository's own config names a command (`_GIT_COMMAND_KEYS`: a filter, a diff textconv); whether inputs changed since the last run, and saved marks. The list itself: `SECTIONS`, `items` (each `Item`'s section, title, `Cmd` commands, how and why; `_spec` holds them in order), `item_ids`, `item_meta`, `STEP_RULES` (the steps no detector proves: `s_configure`, `s_transfers`, `s_ticker_map`, `s_format` (the map the run reads, ticker.map with its tobase.map: `_map_problems` names the first line the run refuses and marks the loader's lines said, `mark_map_problems_said` in `src/taxjson/lib/ticker_map.py`), ... — from the project's files and `reports/run_summary.json`, `src/taxjson/lib/first_run.py` — `collect`, `SUMMARY_FILE`), `_before_run`, `next_result`, `render_guide`, `to_json`, `guide_json`, `SCHEMA_VERSION`, `named_commands`, `EXCLUDED` (every user-facing command named by an item or excluded with its reason; `tests/test_checklist_items.py` checks both ways).
- `src/taxjson/lib/checklist.py` — `d_inputs_frozen` (`_inputs_reach`; done once the year is filed, `_filed_after_year_end`: a late export is an amendment), `d_lock_committed` (`_filed_ignored`: the lock or the reports as filed hidden by `.gitignore`, named with its rule), `s_next_year` (a single-folder project: `migrate --to-years` first): the year-end steps.
- `src/taxjson/bin/taxjson_run.py` — `cmd_checklist` (a DONE mark on a checked step prints that a finding outranks it; a `--done` over a finding this year's kept verdict holds is refused, and `--force` records the forced mark, the walk's `[d]` and `[f]` alike: `src/taxjson/lib/checklist.py` — `kept_verdict`, `finding_over_done`, decided by the verdict's own `question` flag, not the step), `_checklist_config` (taxjson.toml read leniently when the strict loader refuses it: the configure item's finding, and every check blocked; an account that is no table, or a folder link leaving the project, is refused as every command refuses it, `_refuse_outside_dir_links`; `src/taxjson/lib/checklist.py` — `Ctx`, `CONFIG_FIRST`), `_checklist_walk` (exit 1 while a step is open, the marks applied), `cmd_sanity`, `_project_file` (a relative FILE of `sanity` / `opening` looked for in the project folder when the shell's has none), `_sanity_items_from_config`, `_sanity_print_extras`, `cmd_check_dates`, `cmd_edge_cases`, `cmd_spinoffs`, `cmd_splits`: `checklist` (and its interactive walk), `sanity`, `check-dates`, `edge-cases`, `spinoffs` and `splits`.
- `src/taxjson/lib/positions_reports.py` — `read_positions`, `detect_positions`, `PositionsReport`, `PositionRow`, `positions_only`, `toml_as_of`, `PositionsDateError` (an impossible date: sanity refuses the file), `PositionsSnapshotError` (not a whole snapshot: a `holdings_count` that is not its number of tables, a contract size that is not a positive number; sanity refuses the file), `account_key` (the whole broker account id hashed, for telling accounts apart while labels stay masked): broker positions reports (IB, RBC, TOML holdings).
- `src/taxjson/lib/positions_reports.py` — `_RBC_PER_SHARE_RE`, `_RBC_AVERAGE_RE`, `_rbc_cost_label_error`: an RBC cost column read as a total or per share by its label, refused when it is neither.
- `src/taxjson/lib/positions_check.py` — `compare_cost` (`roc_symbols`, `spinoff_symbols`: its reasons), `positions_on`, `book_rows`, `load_base_rows`, `BaseBookError` (`strict`: books that are there but unreadable are refused, never an empty history), `income_share_mismatches` (entitlement on the record date, else `INCOME_WINDOW_DAYS` before the pay date), `load_inventory`: cost and dated-position checks for `sanity` (`book_rows`: the books' rows with the missing-history openings the gains run booked — every dated positions view reads them; `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `_file_day`, `_file_broker`: several snapshots of one broker account compared one date at a time; files of one account at different dates that do not name their broker account refused; `_sanity_console`, `_SANITY_REFUSED`: the run's end check after sanity refused an input; `src/taxjson/lib/checklist.py` — `d_sanity`, `_sanity_differences`: the checklist's step, named by the quantity differences, never the informational COST section).
- `src/taxjson/lib/export_coverage.py` — `find_gaps`, `account_gaps`, `open_positions`, `held_at_broker` (a broker's position counts as far as the account's books hold it), `closed_later` (the account's later rows of any source, `.tt` included, close a position), `next_activity` / `list_command` (the checklist's line: a count, `taxjson list <account> <end>` and the next activity a later file records), `file_source_id` / `row_source_ids` / `add_source` / `source_broker` (a book row's export, by the masked name the parse writes), `_book_renames` (sidecar and corporate-action rows through the books' renames), `info_message`, `ib_statement_end`, `webull_export_end`, `message`, `question_text`, `detail`, `Gap` (`key`: account|broker|end; `expired`: options that expired in the gap), `GRACE_DAYS`, `LAST_ROW_SLACK`: a broker whose exports for an account end before the year end (today in the running year) while it holds positions there; the run's Warning (`src/taxjson/bin/taxjson_run.py` — `_say_export_coverage`, `_checklist_answered`, `_mark_answers`), the checklist's export-coverage step (`src/taxjson/lib/checklist.py` — `d_export_coverage`; a `Result` with `question` set is answered by a DONE mark for the `answers` keys it recorded: `question_answers`, `apply_override`, `QUESTION_STEPS`, `option_keys`).
- `src/taxjson/lib/check_dates.py` — `analyze`, `render`, `check_trade_time`, `asset_class`, `sources`: `taxjson check-dates` (a parsed source the account's sources list names is checked, or an ERROR: `unreadable`, `missing`).
- `src/taxjson/lib/edge_cases.py` — `analyze`, `render_text`, `year_straddles`, `windows_across_year_end`, `_allowed_why`, `deferred_into_next_year`, `calls_in_windows`: `taxjson edge-cases` (the engine's year-end pools or lots, `inventory_year_end`: `src/taxjson/lib/core.py` — `final_year_end_pools`, `_ye_lots`, `_inventory_rows`, Canada's pools and the US lots).

## Audit, wash sales, explain and tax logic

`audit` traces every taxable disposition from the broker row to the reported
gain. `wash-sales` lists superficial losses or wash sales with the replacement
that caused each. `taxjson-explain` prints a calculation trace for one gain.
`tax-logic` prints every rule the code applies, with the project's settings
filled in; it is the spec the code and tests are checked against.

- `src/taxjson/bin/taxjson_audit.py` — `main`, `build_event`, `render_event`, `render_reconciliation`, `build_source_index`, `merge_lot_records`: the audit. What it compares: `tie_out_differences`, `manual_differences`, `TIE_FIELDS` (the saved gains file, field by field and per lot), `money_lineage` (a book row's amount from its parsed row, converted or not), `pool_trade_check` (the audited securities' buys and earlier trades), `source_hits`; what it does not, `SCOPE` (the `--json` `scope`, the footer's `not traced` line).
- `src/taxjson/bin/taxjson_run.py` — `cmd_audit`, `_audit_source_files`, `_merge_audit_json`, `cmd_wash_sales`, `_explain_wash_sales`, `cmd_tax_logic`: `audit`, `wash-sales` and `tax-logic`.
- `src/taxjson/bin/taxjson_explain.py` — `main`, `print_trace`, `gain_matches`, `fmt_summary`: the `taxjson-explain` tool.
- `src/taxjson/lib/tax_logic.py` — `Rule`, `catalog`, `sections`, `render`, `_canada`, `_usa`, `PARTITION_RULES`, `NON_RULE_SETTINGS`, `_sentences`, `_clauses`: the rule statements with their ids; `render` prints a statement's first sentence as its bullet and the rest as items under it.

## Planning before you trade

These views answer "what happens if I trade now": the wash radar shows each
holding's superficial-loss or wash-sale status as of a date; buy-check and
sell-check answer for one symbol; rebuy lists each loss sale whose window is
open with the day it closes; div-estimate projects the coming dividends; harvest shows unrealized gains at current
prices; tips gives tax-efficiency advice for next year (for example a
Canadian dividend payer held through its US listing); watch reports changes
since the last look (cron-able); option-boundary lists written options that
straddle a year end.

- `src/taxjson/bin/taxjson_wash_radar.py` — `main`, `_render_text`, `_render_brief` (the default STATUS table), `_us_engine_losses`, `_advisory_category`, `_CA_DEFINITIONS`: the wash radar. `--show`, `_shown_for_line`, `rows_scoped`: `wash-radar ACCOUNT` and the run's `reports/wash_radar_<account>.*` read every taxable book and show one account's listings (`src/taxjson/bin/taxjson_run.py` — `cmd_wash_radar`, `stage_cross_reports`).
- `src/taxjson/bin/taxjson_run.py` — `cmd_wash_radar`, `_radar_config`, `_radar_engine_args`, `cmd_buy_check`, `cmd_sell_check`, `cmd_rebuy`, `_wash_class_context`, `cmd_harvest`, `cmd_watch`, `cmd_tips`, `cmd_option_boundary`: `wash-radar`, `buy-check`, `sell-check`, `rebuy`, `harvest`, `watch`, `tips` and `option-boundary`. `_radar_taxable_bases` (the taxable books a radar run reads; `_radar_missing_context`, the ones missing, after which `watch` compares and saves nothing: `src/taxjson/bin/taxjson_watch.py` — `baseline_date`), `_not_applicable` / `_no_wash_checkable` (no account the rule reaches: one line, or the command's `--json` document with `not_applicable`; `_empty_radar_json`, `_check_na_json`).
- `src/taxjson/lib/rebuy.py` — `build`, `render`, `any_account_line`, `CLEARED_DAYS`: `taxjson rebuy`, each loss sale whose window is open and its re-entry date, read from the radar's rows (`src/taxjson/bin/taxjson_wash_radar.py` — `reentry_date`, `rebuy_at`, `--cleared-days`; `src/taxjson/bin/taxjson_watch.py` — `flatten_radar`), matched to a query as buy-check matches (`src/taxjson/bin/taxjson_run.py` — `_class_matches`, `canon.members`).
- `src/taxjson/lib/div_estimate.py` — `payments` (one payment per account, broker account, symbol, date and currency; `splits` scale earlier share counts, every split in the books whatever the as-of date, one event per `split_copies`; `qty_by` adds a dividend's shares to a payment in lieu's, `_short_on` tells a short's expense from a reversal by the position `_held` walks: each account's trades, split-adjusted, through renames), `Payments` (re-read as of the estimate's date; `notes` for the flags; `short_paid`, the dividends paid while the books show a short, for the command's warning), `schedule`, `detect_frequency`, `estimate`, `horizon_problem`, `render`, `FREQUENCIES`, `GRACE_DAYS`: `taxjson div-estimate`, upcoming dividends from each held position's own history (`LOOKBACK` payments on a frequency's grid); `src/taxjson/bin/taxjson_run.py` — `cmd_div_estimate` (the native books, the native holdings, `latest_rate`, `horizon_problem`), `_tt_openings` (the units `.tt` `OPENING … cost=unknown` lines open, as the gains stage opens them).
- `src/taxjson/lib/option_yield.py` — `contracts`, `analyze`, `render`, `Lot`, `Close`, `underlying_matches`, `default_size`, `units_on`, `symbol_on` (a lot's dated splits), `_finish` (the capital, an unknown premium): `taxjson option-yield`, an underlying's option trades over a window (contracts matched per account and series FIFO; premiums, realized P/L by how each closed, open contracts, the yield on the cost of the shares held, else the time-weighted strike notional, annualized); `src/taxjson/bin/taxjson_run.py` — `cmd_option_yield` (the native books of every account, the positionals resolved as underlyings / period / account, the window's start and end).
- `src/taxjson/lib/option_yield.py` — `strike_on`, `notional_on`: a renamed series' own strike; `_add_notional_days` (the strike notional of each stretch between a lot's splits and renames), `seq` (the opening's replay order: FIFO when a rename merges two series), `TRANSFERRED`, `transfer_in`, `_kept_transfer_ids` (a TRANSFER row moves contracts out — no money, no P/L — or in, premium unknown; a zero-net cluster is a no-op, as the gains run reads it).
- `src/taxjson/lib/map_hygiene.py` — `symbol_root`, `listing_names`, `pair_verdict`, `APART` (a same-root pair is a candidate unless the exports show it apart — a receipt, different companies), `sightings`, `map_gaps`, `MapGap`, `gap_reason`, `unused_rules`, `UnusedRule`: the listing helpers `tips` and the map checks share; the listing pairs the map does not answer (MAP-GAP, a pair to verify: TOBASE or DISTINCT) and the rules no symbol of the books reaches (root-aware: an option's underlying and a rename chain keep a rule live) that `ticker-map --suggest` lists.
- `src/taxjson/bin/taxjson_safe_to_sell.py` — `main`, `_STATUS`: one line per taxable position: may it be sold at a loss today.
- `src/taxjson/bin/taxjson_harvest.py` — `main`, `load_positions`, `load_radar`, `_recovery_schedule`, `_days_to_long_term`: the harvest view.
- `src/taxjson/bin/taxjson_watch.py` — `diff_radar`, `diff_harvest`, `flatten_radar`, `load_state`, `_state_shape_problem`, `_sheltered_only`, `render_report`: the change detector.
- `src/taxjson/lib/checklist.py` — `_attention_reason` (the checklist's option-boundary line: the first flagged contract's reason); `src/taxjson/lib/option_boundary.py` — `write_lots` (per account and symbol; `factor`, `seq`, `origin` on each `WriteLot`: the splits since the write, the write's replay order, whether the write is in the books), `kept_transfers` (the TRANSFER rows the gains run keeps: a zero-net cluster is a no-op), `TRANSFERRED` (a close that moved the contracts out), `_unknown_rows` (a short opening balance or a transfer in: premium and write year unknown, said so), `straddling`, `expired_open`, `filed_locks`, `WriteLot`, `project_question_rows`, `question_message`, `question_detail`: written options across a year boundary, and the transition question (a contract written before `option_grant_timing_since` closed this year, CA-OPT-11) that `taxjson run` (`src/taxjson/bin/taxjson_run.py` — `_say_option_transition`), and the checklist (`src/taxjson/lib/checklist.py` — `d_option_boundary`) ask.
- `src/taxjson/bin/taxjson_lint_crosslistings.py` — `analyze`, `venue_splits`, `main`: cross-listed holdings the radar would see as two.
- `src/taxjson/lib/price_chain.py` — `fetch_prices`, `fetch_option_prices`, `PriceQuote`, `yf_symbol_for`, `latest_rate`: current prices (IBKR, then yfinance, then the cache).
- `src/taxjson/lib/price_chain.py` — `close_on`, `DayClose`, `OfflineCloseMissing`: one day's close (the close cache, then Yahoo's history) for an in-kind move's value.
- `src/taxjson/lib/price_chain.py` — `provider_unit`, `clean_quote_currency`, `minor_unit_listing`: the one minor-unit table (GBp/GBX, ZAc, ILA -> major) the live quote, the day's close and both caches share.
- `src/taxjson/lib/price_chain.py` — `_usable_price`, `_ibkr_cache_spelling`: a tier's price must be finite; an IBKR hit is cached under the listing it priced.
- `src/taxjson/lib/price_chain.py` — `_quiet_yfinance`, `_yfinance_fetcher`, `_yf_history_fetcher`: the Yahoo tiers, with yfinance's own log and print lines silenced (the callers' labelled warnings say what was missed).
- `src/taxjson/lib/price_chain.py` — `_load_cache`, `_cached_price`, `_save_cache`, `_iso_day_in`: the two caches' read, and the save of a run's fresh entries merged into the file under its lock (`src/taxjson/lib/json_cache.py` — `save_json_cache`); a cached close's day must lie in the asked day's lookback window, and an entry whose unit cannot be read is a miss.

## Filing forms and slips

`form-export` renders gains into filing shapes (Schedule 3 lines for Canada,
Form 8949 and TXF for the US). `t1135` helps with the foreign property form.
`reconcile-slips` compares the broker's T5008 or 1099-B slips with the
computed dispositions; `slip-audit` (Canada) compares the T5 and T3 slips
with the books' income. `carryover` keeps the loss carry-forward ledger.

- `src/taxjson/bin/taxjson_form_export.py` — `main`, `build_schedule3`, `schedule3_line`, `build_8949`, `build_txf`, `filing_lines`: the form renderers; `write_csv` (`--csv`) writes each cell through `spreadsheet_cell` (a formula-looking text cell gets a leading quote).
- `src/taxjson/bin/taxjson_t1135.py` — `build_report`, `render_report`, `render_brief`, `walk_costs`, `classify_country`, `FILING_THRESHOLD`: T1135 cost amounts by country.
- `src/taxjson/bin/taxjson_t1135.py` — `_fold_merger_ratios`, `_Pool`, `eps`, `cost_amount`, `held_in_year`, `unknown_in_year`, `long_in_year`, `short_in_year`, `short_only_symbols`: the cost walk takes a merger's per-account ratios folded as the gains engine does, and a pool's flat tolerance is the engine's (`pool_qty_eps`: a sub-micro coin holding is property); a holding at a known zero cost stays a row, a cost unknown while held stays unknown for the year, and a position held only short (a written option, a short sale) is named instead of a row.
- `src/taxjson/bin/taxjson_t1135.py` — `property_category`, `load_categories`, `CATEGORY_NAMES`, `_cat`: each property's T1135 category (CAT); `src/taxjson/lib/tobase_map.py` — `t1135_kinds`, `NOT_SHARE_KINDS`: the interlisted master's units and funds, left for the user; `src/taxjson/lib/ticker_map.py` — `t1135_category`, `add_side_rule`: the optional CATEGORY of a `T1135` line.
- `src/taxjson/bin/taxjson_reconcile_slips.py` — `reconcile`, `load_slip`, `load_computed`, `check_computed_currency`, `render`, `render_brief`, `SlipRefused`, `ComputedCurrencyRefused`: slip reconciliation (a slip, and a gains file, in another currency than the country's is refused).
- `src/taxjson/lib/slip_audit.py` — `audit`, `load_slips_file`, `load_books`, `ib_slips`, `slip_year` (the year a report component is on the slip of: a T3's by its ex-date, else the pay date), `report_identity`, `annual_average`, `_audit_group`, `_match_payments`, `_pair_in_order`, `_securities`, `_suggest_cgd`, `_suggest_roc`, `question_keys`, `template`, `render`: `taxjson slip-audit` — the slips in inputs/slips/ (slips.toml, IB's dividends reports) against the books' income per account, broker account, currency and box; the suggestions; what could not be compared (`not_compared`: an account with no readable books, a report or slip matched to no account, an unknown IB component; status `incomplete`, exit 1); the checklist's t5-t3 step (`src/taxjson/lib/checklist.py` — `d_t5_t3`, blocked or attention over `not_compared`).
- `src/taxjson/lib/cra_slips.py` — `parse_slips`, `parse_text`, `read_pdf`, `pdf_paths`, `broker_of_issuer`, `issuer_aliases`, `alias_of_issuer`, `book_groups`, `_bare_groups`, `place`, `drop_duplicates`, `plan_import`, `table`: T5 / T3 PDFs downloaded from CRA My Account (`slip-audit --import-cra`, `src/taxjson/bin/taxjson_run.py` — `_slip_audit_import_cra`): read with pdftotext (`pdf_text`: the absolute path after `--`, its text capped at `PDF_TEXT_MAX` and one time limit on reading and exit, `_read_capped`; each slip from its own slip line: the slip line and box rows only), a T5's broker from its issuer (the broker's whole name, or a carrying dealer the shipped `src/taxjson/data/slip_issuers.toml` names; broker accounts with no income rows from the parse metadata, `src/taxjson/lib/slip_audit.py` — `broker_account_files`), placed in an account, broker account and fund by the books' payments, each slip once (a duplicate dropped, an amended slip replacing its original), written as [[slip]] tables with a salted broker key (`src/taxjson/lib/slip_audit.py` — `key_salt`, `broker_key`, `resolve_keys`, `comment_out_tables`); `tjs redact` replaces the key in its copy (`src/taxjson/bin/taxjson_redact.py` — `_redact_broker_keys`).
- `src/taxjson/data/slip_issuers.toml` — `alias`, `broker`, `issuer`, `source`: the shipped issuers that are a broker's carrying dealer (CI Investment Services for Webull Canada), read only by `issuer_aliases`; an issuer carrying a broker's whole name needs no entry.
- `src/taxjson/lib/ib_dividends.py` — `read_report`, `is_dividends_report`, `component_category`, `Payment`: IBKR's dividends report (U*.YYYY.dividends.csv), payment by payment with its T5/T3 split; the holder's name is never read.
- `src/taxjson/lib/ib_dividends.py` — `_iso`, `_num`: the report's dates (calendar-checked) and amounts (strict numbers).
- `src/taxjson/bin/taxjson_carryover.py` — `build_canada_ledger`, `build_usa_ledger`, `load_claimed`, `lock_figure`, `render`, `render_brief`: the carryover ledger; `engine_net_gain` (Canada: the project year's net is the RETURN row the wrapper passes as `--return-row`, `src/taxjson/bin/taxjson_run.py` — `_project_return_row`, `_return_row_totals`); `locked_years` (the locks are read before the gains run: US-WASH-22's add reaching a locked year is booked in the loss's year); `excluded_unknown_cost`, `incomplete_years`, `_no_rows` (the unknown-cost sales every pass left out, the US crypto pass's too).
- `src/taxjson/lib/carryforward.py` — `resolve_losses`, `resolve_amt`, `amt_from_config`, `record_block`, `lock_block` (through `taxjson_filed.read_lock`, with the project's country), `block_problem` (a present section must carry its closing balance; no negative balance; `_by_year_problem`: one entry per year of origin, 0 or more; the minimum tax closing the sum of its years), `handoff_issues`, `partial_lock`: carry-forwards from one year to the next (from a lock taken before its year ended: said provisional).
- `src/taxjson/bin/taxjson_run.py` — `cmd_form_export`, `cmd_t1135`, `cmd_reconcile_slips`, `cmd_slip_audit`, `cmd_carryover`, `_taxable_gains_argv`: the commands.

## Comparing runs

`compare` diffs `reports/` with a snapshot: the run before (kept by every
full run), one saved by name, or the reports as filed.

- `src/taxjson/lib/compare.py` — `take`, `_publish`, `_HIDDEN`, `rotate_previous`, `save`, `load`, `_check_snapshot`, `list_snapshots`, `compare`, `render`, `render_list`, `report_files`, `normalized`, `aggregates`, `filed_reports_dir`, `FIGURES`, `PREV`, `FILED`: the snapshots (`work/compare/<name>/`; staged in a dot-named folder of the call's own, the earlier snapshot moved aside and back when the rename fails; a snapshot's shape checked when loaded), the comparison (undecodable bytes kept distinct) and its views; `src/taxjson/bin/taxjson_run.py` — `cmd_compare`: the command, and the rotation at the start of `_cmd_run`; `src/taxjson/lib/checklist.py` — `recorded_fingerprint`, `fingerprint_delta`, `input_fingerprint`: what a snapshot records the reports were built from and how two fingerprints are compared.

## Filed-year lock and year hand-off

`close-year` snapshots a filed year's figures; every later run and
`check-filed` recompute the year and report drift. `handoff` records what a
closed year carried forward and checks that the next year's project starts
from exactly that.

- `src/taxjson/lib/channels.py` — `running_build`, `build_label`, `same_build`; `src/taxjson/bin/taxjson_run.py` — `_closed_with`: the taxjson a lock was closed with (its `taxjson` record), named on a drift.
- `src/taxjson/bin/taxjson_filed.py` — `write_snapshot`, `recompute_year`, `diff_snapshot`, `project_locks`, `lock_for_year`, `aggregates_from_gains`, `lock_shape_problem`, `_finite_num` (a locked figure that is not a finite number is a damaged lock, in the shape check and the drift check), `partial_year_note`, `income_as_summed` (the income close-year and `years` show: `sum`'s taxable SUBTOTAL, each account's cents): the filed-year lock.
- `src/taxjson/bin/taxjson_filed.py` — `read_lock`, `LockProblem`, `lock_damage`, `load_lock_json`, `file_year`, `lock_country_problem` (`prior`: the advice of a next-year reader): the one check of a lock every reader applies — check-filed and the run's drift check, `years`, the next year's carryover, estimate / amt / close-year and handoff — so a lock one refuses is never sound to another; `src/taxjson/bin/taxjson_run.py` — `_check_filed_years`, `cmd_amt`; `src/taxjson/lib/project_layout.py` — `years_report`; `src/taxjson/bin/taxjson_carryover.py` — `lock_figure`; `src/taxjson/lib/carryforward.py` — `lock_block`; `src/taxjson/lib/handoff.py` — `validate_record`, `load_record`: its readers.
- `src/taxjson/bin/taxjson_run.py` — `cmd_close_year`, `_close_year_attention` (the checklist steps before the lock that need attention: asked on a terminal, refused without one unless `--yes`), `cmd_check_filed`, `_check_filed_years`, `_carryforwards_for_lock`, `_as_filed` (the net capital loss from `--filed-dispositions`), `_locked_year_flags`, `cmd_handoff`, `_prior_record_path`, `_handoff_gains_flags`: `close-year`, `check-filed` and `handoff`, and the drift check every run makes of each filed year.
- `src/taxjson/lib/handoff.py` — `snapshot`, `check`, `render`, `validate_record`, `straddlers`, `load_filed_dispositions`, `filed_rows_outside`: the year-to-year record and its check (`snapshot`: the Dec 31 pools, a US crypto group without the wash-sale rule; Canada's superficial-loss landings booked as `WASH_HANDOFF_` rows so the engine's own `deferred_wash` tally releases them; a filed disposition must be of the year closed).
- `src/taxjson/lib/project_layout.py` — `frozen_year` (a damaged or deleted lock counts while taxjson.toml carries the freeze note: `lock_missing_frozen`, `lock_missing_parts` for what `years`, check-filed, new-year and the checklist say), `frozen_project`, `tobase_layout` (`frozen`: a filed year's own tobase.map is the freeze, never a copy for `migrate` to merge, even beside a setting written back; `years_report` says so as `tobase_frozen`), `_FROZEN_KEYS` (a frozen year's `inputs_dir` / `tobase_map`: never compared by `compare`, so `years` and `align` leave them out), `tobase_both_problem` (a filed year naming the shared tobase.map again is told to delete the lines, never sent to `migrate`).
- `src/taxjson/lib/project_layout.py` — `_freeze_noted`, `FROZEN_NOTE_PREFIX` (a year whose lock cannot be read stays frozen while its taxjson.toml carries the freeze note), `record_filed_check`, `filed_check`, `FILED_CHECK_STATE` (the drift check's verdict on each filed year, in work/, with the lock it judged; `years_report` says it as `drifted`); `src/taxjson/bin/taxjson_run.py` — `_check_filed_years` (records it).
- `src/taxjson/bin/taxjson_run.py` — `_freeze_inputs`, `_one_frozen_note` (one note over adjacent keys), `_keep_filed_reports`, `_frozen_summary`, `_frozen_record`, `_frozen_inputs_changed`, `_unfreeze_text`, `_FROZEN_NOTE`, `_FROZEN_NOTE_2`, `_gitignore_hides_filed_reports` (an older scaffold's unanchored `reports/` line; `_TEMPLATE_GITIGNORE` is anchored), `_thaw_partial_freeze`, `_thaw_text` (a partial lock an older release froze, thawed by the re-close): close-year's freeze of the inputs (a year that has not ended is hashed only: a `partial` record, which `_frozen_record` here and project_layout's frozen-year check do not count as frozen) (the shared files copied into the year's own `inputs/`, `inputs_dir` / `tobase_map` commented out, the hashes in the lock's `frozen` record), the reports as filed, the frozen-input note of the drift check (`_frozen_settings_back`: a frozen year whose taxjson.toml names the shared folders again), and how `new-year` and `migrate --to-years` read the record; `src/taxjson/lib/checklist.py` — `input_files`, `fingerprint_changes`, `frozen_files` (the frozen record holds files only, never the US locked-years entry), `_same_tobase`, `_fp_key`, `_inputs_folder_name` (a shared `inputs/` keyed as the setting names it, a symlink included, so the frozen copy's keys match): the files the freeze copies and the comparison of a lock's frozen hashes with the project now.

## Redact

`redact` strips account numbers, names and contact details from a broker
export while keeping every row's shape, so a file can be shared as a parser
sample or a bug report. It is a best-effort pattern matcher.

- `src/taxjson/bin/taxjson_redact.py` — `redact_file`, `redact_text`, `redact_tree`, `compile_patterns`, `Pseudonyms`, `load_denylist`, `identity_counts` (`IDENTITY_KINDS`: every kind a consumer such as the generate-parser gate counts): the redactor; account-id placeholders `_known_placeholder` (never an id of the input: `IdTable.reserved`, filled for a project copy by `_tree_ids`) replaced in one pass by `_id_subber` (`redact_text`, `redact_project_text`, `_sweep`); wallet addresses `_WALLET` (pseudonymised when `_plausible_wallet`), leftovers `_WALLETISH` / `_wallet_like` (review); the review pass `_review` (`_caps_name`, `_LAST_FIRST`, `_from_to_name`: a bare `FROM NAME` / `TO NAME`); ids after a broker name `_BROKER_ACCOUNT`; a year's own taxjson.toml, ticker.map and tobase.map in the project copy: `redact_project_text` (the configuration's ids from `config_ids`, comments, denylist / `--also` matches; the copied taxjson.toml must read, `_valid_copy_config`); every redacted export read again by its parser and its numbers compared with the original's, `numbers_changed` (an option's strike is no house number, `_sub_address`); the project copy walks `inputs/` with `_walk_inputs` (a file link leaving it is skipped, `_inside`); in a year folder of a shared-exports project `_project_tree` (with `_PROJECT_FILES`) builds that year as a single-folder project, a link in the shared inputs allowed within the folder holding the years.
- `src/taxjson/bin/taxjson_run.py` — `cmd_redact`: `taxjson redact`.

## Configuration: taxjson.toml, init, format and migrate

taxjson.toml holds `[settings]` (country, year, tax date basis ...) and one
table per account. Every reader goes through the same checks. `init` writes
a new project from a template; `format` re-renders an existing file into the
template keeping the user's values; `migrate` folds an old project's separate
files into ticker.map and taxjson.toml.

- `src/taxjson/bin/taxjson_run.py` — `load_config`, `validate_config`, `_normalize_settings`, `_warn_config_tables`, `_refuse_bad_account_types`, `cmd_init` (a folder made for the project is made owner-only), `_render_init_config`, `_refuse_init_in_year_folder`, `_init_filing_hint`, `_sibling_grant_since`, `cmd_format`, `_backup_config`, `cmd_migrate`, `_migrate_steps`, `_migrate_plan`, `_migrate_done_note`: reading and checking taxjson.toml; `init` (refused in an empty folder named like a year; from January to April a line for filing last year; `option_grant_timing_since` left commented unless another year folder sets it), `format` and `migrate` (every phase — the project's old files, missing_history.json, the shared tobase.map — planned before the first writes; a later refusal names what an earlier phase did).
- `src/taxjson/lib/demo.py` — `ACCOUNTS`, `SCENARIOS`, `files`, `data_dir`, `settings`; `src/taxjson/bin/taxjson_run.py` — `_init_demo`: `taxjson init --demo`, a Canadian 2024 year folder of made-up exports (`src/taxjson/data/demo/<account>/`, the broker demos of `examples/`, a LIRA (Questrade) and an RRSP (IB) of its own, plus an IB statement with the getting-started scenarios and its saved spin-off election in `manifest.json`).
- `src/taxjson/bin/taxjson_run.py` — `_grant_since_warning`, `_books_write_options`, `_grant_since_after_year`: the run's warnings about `option_grant_timing_since` (not set while the books hold a written option; later than `year`).
- `src/taxjson/lib/config_check.py` — `settings_problems`, `account_type_problems`, `account_name_problem`, `ACCOUNT_TYPES`, `ACCOUNT_KEYS`, `RETIRED_SETTINGS`: the checks every config reader applies.
- `src/taxjson/lib/config_template.py` — `SETTINGS_SPEC`, `ACCOUNT_SPEC`, `TABLES`, `render_init`, `format_config`, `scaffold_document`, `Key`: every key taxjson reads, documented per country.
- `src/taxjson/lib/migrate.py` — `plan`, `apply`, `Plan`, `legacy_files`, `LEGACY_FILES`, `MigrateError`: moving old files into the new places.
- `src/taxjson/lib/safe_write.py` — `write_user_file`, `write_atomic`, `atomic_open`, `backup_copy` (each `.bak` name taken with an exclusive create, so overlapping callers never share one), `file_lock` (a link at the lock name replaced, `LockLinkError` when it cannot be), `OutsideLinkError`: writes that never follow a planted symlink, each through a temp file of its own (overlapping writers never share one), with a backup; `file_lock` serializes a read-modify-write. Generated state in `work/` goes through `write_atomic`; `tests/test_fix_issues_safe_writes.py` lists the reviewed direct writes left in the core.
- `src/taxjson/lib/tomlcompat.py` — `tomllib`: the tomllib or tomli import.
- `src/taxjson/lib/requires.py` — `FEATURES`, `INIT_FEATURES`, `problem`, `features_of`, `raised`, `raised_text`, `raise_requirement`, `lowers`; `src/taxjson/bin/taxjson_run.py` — `_refuse_newer_projects`, `_refuse_newer_config`, `_init_requires`, `_raise_requirements`, `_unknown_setting_warnings`: `[settings] requires_taxjson`, the oldest taxjson a project runs on — refused by every command of an older one before anything else; FEATURES lists the release that first reads each layout feature (one line per layout change), and init, new-year, migrate, update-tobase-map, find-missing-history and align raise the key for what they write, never lowering it. An unknown `[settings]` key is one warning line in every command.

## One folder of exports for every year

`init` (by default) writes inputs/<account>/ shared by year folders, each a
complete project whose `[settings] inputs_dir` / `holdings_dir` /
`exports_dir` name the shared folders, and in Canada `tobase_map` the
tobase.map beside the year folders that every year reads. Every input
path goes through lib/project_layout; each year keeps its own
taxjson.toml and ticker.map (`compare` compares the `tobase_map`
setting, and counts tobase.map lines only of per-year copies, the layout
before v0.27.1).

- `src/taxjson/lib/project_layout.py` — `inputs_dir`, `shared_inputs`, `ticker_map_path`, `data_file`, `slips_dir`, `holdings_folder`, `exports_folder`, `project_path`, `write_boundary`, `folder_setting`, `setting_problems` (a folder leaving the folder that holds the project; a year folder holding another year; a year folder holding its own tobase.map beside the shared one, `tobase_both_problem`), `tobase_setting`, `tobase_map_path`, `shared_tobase`, `tobase_shown`, `tobase_layout`, `project_map_path`, `exports_overlap` (an `exports_dir` overlapping the inputs, holdings, a year folder or the project's own folders), `unconfigured_inputs`, `local_input_folders`, `project_of_input`, `is_account_folder` (the project an input file belongs to: the one `TAXJSON_PROJECT_ROOT` names, which `src/taxjson/bin/taxjson_run.py` — `load_config` sets for every stage, when the file is in its shared inputs folder), `year_dirs`, `multi_root`, `new_year_text`, `compare` (`flat_paths`, `account_order`, `map_rules`: an unreadable file is an error), `set_key_text` (every taxjson.toml edit: inline tables, dotted keys, arrays of tables; the result read back with `parse_toml` and compared with `_with_key`, else LayoutError), `toml_statements` (a taxjson.toml's tables and keys by line, quoted names and values over several lines included), `years_report`: where a project's files are, and the year-folder tools.
- `src/taxjson/bin/taxjson_run.py` — `_single_folder_project`, `_single_folder_hint` (a project folder that is no year folder: `years` / `new-year` point at `migrate --to-years`), `cmd_years`, `_years_table`, `_years_tobase` (the tobase.map lines of both views: shared, per-year copies, a filed year's frozen copy), `cmd_new_year` (`_new_year_publish`: made in a staging folder, moved into place when complete; `_implicit_grant_since`; `_templated`: the copied config in the template layout), `cmd_align` (every edit checked before any file is written; `_align_write_result`: the `--write --all --json` result, printed after the writes), `_years_folder`, `_refuse_years_root`, `_YEARS_ROOT_CMDS`, `_refuse_folder_settings`, `_migrate_to_years`, `_write_exports`, `_EXPORT_PATTERNS`, `_exports_manifest`, `_EXPORTS_MANIFEST` (the files the last export wrote; only those are replaced), `_shared_inputs_note` (a file written into the shared exports applies to every year; `_frozen_years_apart` names the frozen filed years it does not reach), `_sends_tt_shown`, `_sends_json_shown` (where the crypto-sends files are): `years`, `new-year`, `align`, the refusal in the folder holding the years, `migrate --to-years` and the newest year's exports/; `src/taxjson/bin/taxjson_redact.py` — `_project_tree`, `_copy_holdings_lists` (a year folder redacted as a runnable single-folder project).
- `src/taxjson/bin/taxjson_run.py` — `_years_as_filed` (a filed year's differences from the newest year are its own: `(filed)`, never a step), `_years_act` (an `! ` line within 100 columns, cut at a clause or word), `_align_filed_year` (`align --write` in a filed year warns first): `years` and `align` around a filed year.
- `src/taxjson/lib/holdings_dir.py` — `discover`, `listed_files`, `folder_for`, `broker_ids`, `snapshot_files`, `empty_snapshot`: the year's holdings/ snapshots `sanity` finds by `[meta] broker_account`, `[meta] account` or file name (a snapshot of an account holding nothing: `[meta]` alone) (`src/taxjson/bin/taxjson_run.py` — `_sanity_items_from_config`, `_holdings_folder_in_use`).

## The Canada and USA partition

Canadian and US rules never mix. One module resolves a project's country and
owns the tables saying which settings, config tables, flags, commands and
project files belong to which country. `scripts/check_tax_rules.py` checks
that the tables are complete.

- `src/taxjson/lib/country.py` — `settings_country`, `canonical_country`, `CountryError`, `SETTING_COUNTRY`, `CONFIG_COUNTRY`, `FLAG_COUNTRY`, `COMMAND_COUNTRY`, `PROJECT_FILE_COUNTRY`, `config_country_problems`, `check_engine_allowed`, `refuse_foreign_flags`, `default_tax_date`, `basis_pooled_across_accounts`, `lot_rule`: the one country resolver; the ownership tables (settings, config tables, flags, commands, project files); the checks built on them; per-country defaults.
- `src/taxjson/bin/taxjson_run.py` — `_country`, `_tax_date`, `_is_us`, `_refuse_other_country_books`: how the CLI reads them.

## Markets, calendars and dates

Venue suffixes, currencies and the security lists taxjson cannot read from an
export live in one data file, overridable from ticker.map. Settlement dates
use each market's holiday calendar and the T+3, T+2, T+1 history.

- `src/taxjson/lib/markets.py` — `data`, `overrides`, `suffix_of`, `suffix_currency`, `is_canadian_listing`, `split_share_roots`, `contract_size`, `usd_unit_listing`: market reference data (and the convention for a Canadian-listed fund's US-dollar units).
- `src/taxjson/data/markets.toml` — `venues`, `currency_suffix`, `usd_unit_class`, `ib_venues`, `kraken_assets`: the shipped defaults.
- `src/taxjson/lib/market_calendar.py` — `add_settlement_days`, `is_settlement_day`, `is_trading_day`, `nyse_holidays`, `tsx_holidays`: settlement calendars.
- `src/taxjson/lib/dates.py` — `settlement_date`, `settlement_lag_days`, `date_to_epoch`, `market_of`, `last_trade_date_settling_by`, `as_of_date_problem`: date helpers for the radar and planning views (a `--date` whose window leaves the calendar is refused).

## The fetch plugin

The core holds no broker API client and reads no broker credential.
`taxjson fetch` finds installed fetchers through the `taxjson.fetchers` entry
point; the separate taxjson-fetch package provides Questrade and IBKR Flex
downloads.

- `src/taxjson/lib/fetchers.py` — `discover`, `Fetcher`, `FetchRequest`, `ENTRY_POINT_GROUP`, `add_fetcher_arguments`, `listing`: the core side of the plugin contract.
- `src/taxjson/bin/taxjson_run.py` — `cmd_fetch`, `_no_fetcher_exit`, `_fetch_plugin_note`, `_fetch_brokerages`: `taxjson fetch`.
- `packages/taxjson-fetch/src/taxjson_fetch/plugin.py` — `BrokerFetcher`: the object the core loads.
- `packages/taxjson-fetch/src/taxjson_fetch/command.py` — `run`, `_run`, `_run_locked`, `_merge_csv_text`, `_questrade_token_file`, `_qt_open_session`, `_questrade_token_write`, `_flex_lost_dates`, `_qt_window_overlap`, `_qt_trim_file`, `_qt_live_holdings`: the fetch command (downloads into `request.inputs`, the shared inputs folder of a multi-year project; `--positions` into the year's holdings folder; one per project under `work/.fetch.lock`, taken by `_run` around the read-merge-publish step itself, `_run_locked`; the token's read, refresh and save under a lock beside the token file; merging a re-fetch into the existing CSV; `--trim-overlap` backups through `backup_copy`).
- `packages/taxjson-fetch/src/taxjson_fetch/api.py` — `qt_refresh`, `qt_activities`, `qt_positions`, `flex_fetch`, `positions_to_holdings_toml`, `mask_account_number`, `scrub_detail`, `write_private`: the Questrade and Flex API clients (`scrub_detail` masks tokens and account ids in every broker error text), and the owner-only atomic writer of fetched files.
- `packages/taxjson-fetch/pyproject.toml` — `taxjson.fetchers`: the entry-point registration.

## Release channels and install

`main` is the development line; a `vX.Y.Z` tag is a release. channels.json
names the release each channel (stable, beta) points at. The installer clones
a channel's release; `release.sh --prepare` lands the version commit after
the full gate, and `release.sh` (the cut) tags it once GitHub CI passed on
it and pushes the tag to the private dev repo (`origin`); the publisher
publishes it to the public repo after its gate and wait, and the public
repo's `release.yml` makes the GitHub release; `promote.sh` moves a
channel and asks the publisher for it with a fast-lane tag; `land.sh` is how
both (and every change) reach the dev `main`, by pull request.
docs/releasing.md has the steps.

- `install.sh` — `main`, `newest`, `named`, `vernewer`, `--channel`: the one-line installer.
- `install.sh` — `DRY_RUN`, `DRY_TMP`, `mktemp -d`: `TAXJSON_DRY_RUN=1` resolves the channel and stops; with no install yet it clones into a temporary folder removed on exit, never `TAXJSON_DIR`.
- `install.sh` — `pip_locked`, `LOCKS`, `.locks-from-main`, `--require-hashes`, `--no-build-isolation`: every dependency from the hash-pinned locks (wheels only), then taxjson and taxjson-fetch `--no-deps` from the checkout; a hash mismatch stops, a missing wheel falls back to the pinned core.
- `requirements/fx.txt` — `inputs-sha256`, `body-sha256`, `--hash=sha256`: one lock per extra (and `build.txt`, `core.txt`), universal over platforms and Python 3.10+.
- `scripts/lock_deps.py` — `lock_inputs`, `compile_locks`, `check`, `coverage`, `PLATFORMS`, `PYTHONS`, `REQUIRED`: regenerates the locks with uv, checks them against pyproject.toml (offline) and checks wheel coverage (network).
- `channels.json` — `stable`, `beta`: where each channel points.
- `scripts/kit_checks.py` — `CONFIG`, `SCRIPT_PATH`, `osv-scanner`, `opengrep`: the publish kit's dependency and code checks, an unchanged copy of the kit's `checks/kit_checks.py` (kit doctor compares the two); every hit blocks unless `kit-checks.toml` (`lock_files`, `code_exceptions`, `vuln_exceptions`) excepts it. Run by `.github/workflows/kit-checks.yml` on the dev repo's pull requests (`github.event.repository.private`, `taxjson-dev`) and by `kit preflight` before a tag.
- `scripts/release.sh` — `--prepare`, `## Unreleased`, `CHANGELOG.md`, `scripts/ci.sh`, `scan_notes`, `make_notes`, `publish_main`, `tag_free`, `github-ci.sh`, `release_edits_only`, `--fresh-gate`, `--gate-max-age`, `PUBLIC_REMOTE`: a release in two steps — `--prepare` (CHANGELOG heading, version bump in both packages, full gate — or a reused PASS of the same tree plus `ci.sh --release-edits` — the version commit, landed through `publish_main`), then the cut (the version commit found on main by content, green GitHub CI on its push, a tag name free on the public repo too, scanned notes, the tag pushed to the dev repo).
- `scripts/release-notes.sh` — `## $TAG`: a release's notes, the CHANGELOG section (release.sh scans them; the public repo's release.yml publishes them).
- `.github/workflows/release.yml` — `gh release create`, `release-notes.sh`, `github.repository == 'taxjson/taxjson'`: the GitHub release for a tag the publisher pushed, on the public repo only.
- `scripts/land.sh` — `gate`, `fail`, `TAXJSON_LAND_DIRECT`, `gh pr create`, `--auto --rebase`, `expected`, `checks`, `origin_main`, `git pull -q --rebase --autostash`: lands local main's new commits through a pull request (the pre-push gate, a branch, rebase-merged once its checks pass, only onto the base it pushed on: the Actions jobs of the pushed commit's runs on that branch and its commit statuses, not `gh pr checks`; a pull-request workflow with no run yet is pending; a failed check closes it).
- `scripts/private-values` — `denylist`, `expand`, `private_json`, `email_allowlist`: the publisher's fingerprint provider (`kit values sync` runs it; it prints values): the denylist's finite lines and `private.json`'s addresses, without the owner's names, noreply addresses and allowlisted addresses (public). Tested by `tests/test_private_values.py`.
- `scripts/github-ci.sh` — `ask`, `--appear-wait`, `head_sha=$SHA&event=push&branch=main`, `actions/runs/$id`, `TAXJSON_CI_WAIT_MINS`: did tests.yml pass on the push of this commit to main (release cut and promote); a run still going is waited for up to a time limit.
- `scripts/promote.sh` — `die`, `channels.json`, `ci_gate`, `public_gate`, `undo_promote`, `scripts/land.sh`, `fast_lane`, `is not published yet`: points a channel at a published release (main = origin/main, the tag on origin/main and on the public repo, green CI forward), landed by pull request, then a `channels-…` fast-lane tag for the publisher.
- `scripts/channels.sh` — `taxjson channels`: the channel page from a checkout.
- `src/taxjson/lib/channels.py` — `PUBLIC_REMOTE`, `fetch`, `read_channels`, `parse_channels`, `release_tags`, `status`, `render`, `dev_checkout`, `check_deployed`: `taxjson channels`, the checkout `promote` and `deploy` use, and deploy's version check.
- `src/taxjson/bin/taxjson_run.py` — `cmd_channels`, `cmd_promote`, `cmd_deploy`, `_run_script`, `_RELEASE_CMDS`: the release commands.
- `scripts/dev-setup.sh` — `venv/bin/pip install`, `install_hook`, `--hook-only`: developer setup (venv, editable installs, the pre-push hook in the clone's own hooks folder).

## The CI gate and repository checks

`scripts/ci.sh` is the gate: lint, consistency, tax-rules, PII scan, the full
suite (core and the fetch plugin, in an empty HOME, offline; the core suite
in parallel), the extras-sensitive tests with the extras hidden, and the
fuzzers. A push must see its result line PASS; a full PASS from a clean tree
is recorded by tree hash for release.sh to reuse. The pre-push hook scans
what a push would publish.

- `scripts/ci.sh` — `stage`, `finish`, `fuzz_run`, `--nightly`, `--serial`, `--release-edits`, `SUITE_ENV`, `stage no-extras`: the gate's stages.
- `scripts/run_tests_parallel.py` — `discover_modules`, `plan`, `child_env`, `parse_result`, `_COLLECT`, `DURATIONS`, `SPLIT_OVER`: the suite on N processes (a process per module, its own TMPDIR and HOME, longest first, the count checked against discovery).
- `scripts/gate-record.sh` — `gate_dir`, `write`, `find`, `FULL_MODES`: the PASS records by tree hash (`~/.cache/taxjson-gate`).
- `scripts/ci_no_extras.sh` — `hide_extras`, `EXTRAS_TESTS`, `NONET`, `env -i`: the tests that touch an optional extra, run as a core install (no extras, empty HOME, no network).
- `tests/_hermetic/__init__.py` — `install`, `rate_cache`, `SYNTHETIC_RATES`: the synthetic HOME (offline, made-up exchange rates) every test process runs in.
- `scripts/check-consistency.sh` — `CHANGELOG`, `channels.json`: versions, CHANGELOG heading and channels agree.
- `scripts/check-pii.sh` — `main`, `report`, `amount_filter`, `sin_filter`, `entropy_filter`, `CRED_RE`, `--diff`, `mask_hit`, `locate_added`: the personal-data and secret scan (tree, diff, messages); a hit shows its file and line, its content masked.
- `scripts/check-public.sh` — `SPLIT_PY`, `gh api --paginate`, `check-pii.sh`, `--days`, `commits`: scans release notes, issues, pull requests, comments and (with `--days`) recent commit messages on GitHub (read-only).
- `.gitleaks.toml` — `useDefault`: CI's gitleaks job's config (`.github/workflows/tests.yml`, job `secrets`); no exceptions, an exception is an exact value per rule and file.
- `scripts/gitleaks-regression.sh` — `rnd`, `WANT_FOUND`, `WANT_QUIET`: proves a gitleaks config still reports ordinary secrets whatever they are called (CI runs the base commit's copy on a pull request).
- `scripts/hooks/pre-push` — `refuse`, `tag_refused`, `check-pii.sh`: the pre-push hook (the tag guard, then the PII scan).
- `scripts/check_tax_rules.py` — `main`, `collect`, `ownership_problems`, `read_ids`: every tax-logic rule has a test that cites it, and the country tables are complete.
- `tests/tax_rules/__init__.py` — `rule`, `rule_absent`: the test markers that cite rule ids.
- `tests/tax_rules/baseline-unpinned.txt` — `CA-`: the shrink-only list of rules without a test yet.
- `scripts/style/survey.py` — `main`: runs every command on the synthetic style projects and saves the output.
- `scripts/style/measure.py` — `measure`, `is_table`: ranks those captures by style problems.
- `scripts/mutation_audit.py` — `TARGET_FUNCS`, `find_sites`: the mutation harness (mutates engine files in place; run only with `--yes` and restore from git if interrupted).
- `scripts/mutation_triage.py` — `main`: groups mutation survivors for review.
- `.github/workflows/tests.yml` — `check-pii.sh`, `check_tax_rules.py`, `gitleaks-regression.sh`, `pr-commits`, `ubuntu-24.04`: the same gate on pull requests and pushes to main (gitleaks and the per-commit PII scan with the base commit's config and scanner); every job that runs on a pull request runs on every one (land.sh waits for them all); the nightly `schedule` runs on the public repo only (`github.repository == 'taxjson/taxjson'`); the dev repo's jobs run on its self-hosted runner (`taxjson-dev`), each in a fresh virtualenv, and gitleaks is pinned per `RUNNER_ARCH`.
- `.github/workflows/privacy-watch.yml` — `check-public.sh --days 2`, `schedule`: the daily read-only scan of what the public repo serves beside the code (daily there only).
- `tests/test_fix_release_hardening.py` — `TestWorkflowPins`, `TestRunners`, `TestEveryPullRequestCheck`, `TestGitleaks`, `TestReleaseWorkflow`, `TestLand`, `TestCheckPublicDays`: action and runner pins, every pull-request check on every pull request, gitleaks, release.yml, land.sh.

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
- `tests/test_dependency_locks.py` — `TestLocksMatchPyproject`, `TestCheckCatchesDrift`, `TestInstallerUsesTheLocks`: the locks match pyproject.toml's extras and taxjson-fetch's dependencies, are not hand-edited, and the installer installs only from them.
- `tests/test_check_pii.py` — `TestTreeScan`, `TestDiffAndPush`: the PII scanner.
- `tests/test_knowledge_pack.py` — `TestArchitectureMap`, `TestReferences`: every path and symbol this map names still exists.
- `tests/test_fast_gate.py` — `TestPlan`, `TestRunner`, `TestGateRecord`, `TestReleaseReusesAPass`: the parallel runner, the PASS records and their reuse by release.sh.
- `run_tests.sh` — `unittest`: runs the suite with the project venv.
