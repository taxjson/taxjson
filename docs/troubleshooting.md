# Troubleshooting

Known problems, as the person running taxjson sees them, with what causes them and what to do.
Search this file for the exact text you see: a console line (`Error: …`, `Warning: …`, `Info: …`), a
`tjs checklist` step or a line in a `.sum` file. Variable parts (symbols, accounts, dates, amounts)
are synthetic examples here.
Each entry says how to **check** it is this problem: run that first, since several symptoms share
their wording. Most problems are an input (a missing older export, a transfer in, an election not
made), not a bug.
**Fixed in** names the release whose code fixed it: on an older install (`tjs --version`) the fix
starts with upgrading (re-run the installer). `—` means a setting, an input or the design;
`unreleased` means fixed on `main`, in the next release.
**Code** names the file and the function or message to search for; `docs/architecture-map.md` maps
the rest. taxjson computes and shows its work; it gives no tax advice. The rules it applies are in
`tjs tax-logic` (with `--ids`).

## Setting up a project

### "Error: no taxjson.toml in …/taxes/2025. Run `taxjson init` first."
- **Check:** `ls taxjson.toml` in the folder you ran from; read-only commands say "no gains files, and no taxjson.toml in … — not a taxjson project".
- **Cause:** taxjson runs on the project in the current folder (or the one `-C DIR` names), and this folder has no `taxjson.toml`.
- **Fix:** `cd` into the project folder or pass `-C ~/taxes/2025`; for a new project, `tjs init --country canada --year 2025`.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `load_config`, `no taxjson.toml in`

### "Error: [settings] country is missing — set it to "canada" or "usa"", "Error: missing [settings] year in taxjson.toml" or "Error: [settings] year = 2204 is not a plausible tax year (expected 1900..2027)"
- **Check:** every command stops at "Checking the project" (exit 1). An invalid country reads "[settings] country must be canada, ca, usa or us, got 'Ontario'".
- **Cause:** `country` and `year` are required and never guessed. The country decides every tax rule, the base currency and which settings are valid; the year must be a whole number from 1900 to next year (a typo such as 2204 would build empty books).
- **Fix:** under `[settings]`: `country = "canada"` (or `"usa"`) and `year = 2025` (unquoted). The province goes in `province`, not `country`.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/country.py` — `canonical_country`, `taxjson never guesses the country`; `src/taxjson/lib/config_check.py` — `settings_problems`, `is not a plausible tax`; `src/taxjson/bin/taxjson_run.py` — `missing [settings]`

### "Error: this project still has crypto_ticker.map (now ticker.map CRYPTO lines) — these files are no longer read"
- **Check:** every command stops (exit 2) with "nothing was run". The same happens for `yf_ticker.map`, `ticker_extraction_overrides.txt`, `t1135.map`, `amt_carryover.txt`, `claimed_losses.txt`, `capital_gains_dividends.map` and `distributions.map`.
- **Cause:** since v0.17.0 these per-purpose files are `QUOTE` / `CRYPTO` / `EXTRACT` / `T1135` lines in `ticker.map` and tables in `taxjson.toml`. Running past an old file would silently drop its rules.
- **Fix:** `tjs migrate --dry-run` to preview, then `tjs migrate`: it appends the lines and renames each old file to `<name>.migrated`. Review and commit `ticker.map` / `taxjson.toml`, then `tjs run`.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/migrate.py` — `legacy_message`, `this project still has`; `src/taxjson/bin/taxjson_run.py` — `_refuse_legacy_project_files`

### "Warning: taxjson.toml: unknown [settings] key 'taxdate' is ignored (did you mean 'tax_date'?)"
- **Check:** the same form names other places: "unknown top-level table [estimates] is ignored (did you mean 'estimate'?)", "unknown [accounts.qt] key 'transfer' is ignored (did you mean 'transfers'?)". `tjs format` lists the keys the template does not know.
- **Cause:** a misspelled key or table is not read, so its default applies (an `[estimates]` table means 0 other income in `tjs estimate`).
- **Fix:** correct the spelling as suggested. `tjs format` shows every key the country's projects read, with its description and default.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `validate_config`, `_config_table_warnings`, `unknown top-level`

### "Error: [settings] ric_january_dividends is United States-only (…); this project is country = "canada" — remove it"
- **Check:** every command stops at "Checking the project" (exit 1). The reverse happens for a Canada-only key such as `province`, `option_premium_timing` or `corporate_distributions` in a US project, and for a `base_currency` that is not the country's.
- **Cause:** each setting and table belongs to one country (or both); a key of the other country would be silently ignored or applied under the wrong rules, so it is refused.
- **Fix:** delete the line, or fix `country` if that is what is wrong. `tjs format` shows only the keys this country reads.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/country.py` — `_owner_problem`, `config_country_problems`, `SETTING_COUNTRY`

### "Warning: taxjson.toml: inputs/joint/ contains data but has no [accounts.joint] section"
- **Check:** the next line says "It will NOT be processed"; no `==> joint` step appears in the run.
- **Cause:** each folder under `inputs/` is read only when `taxjson.toml` has an `[accounts.<folder name>]` table for it. The folder name is the account name.
- **Fix:** add `[accounts.joint]` with `type = "taxable"` (or `"sheltered"`; `crypto = true` for Coinbase or Kraken files), or move the files into an existing account's folder. Then `tjs run`.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `validate_config`, `contains data but has`

### "Error: account 'qt' has no crypto flag in taxjson.toml but its inputs contain coinbase files" (or "has crypto = true … contain questrade files")
- **Check:** the next line names each file and how it was detected, e.g. `inputs/qt/coinbase_demo.csv (coinbase: content: columns Timestamp,Transaction Type,Asset…)`.
- **Cause:** crypto exchange exports (Coinbase, Kraken) go through the crypto pipeline (price lookups, coin pools) and equity exports through the securities pipeline, chosen by the account's `crypto` flag. A file in an account of the other kind would be booked wrongly, so the run stops.
- **Fix:** move the file to an account of the matching kind (`crypto = true` under `[accounts.<name>]` for Coinbase and Kraken), or fix the account's `crypto` flag.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `stage_account`, `_CRYPTO_BROKERS`, `in taxjson.toml but its inputs`, `routed through the wrong pipeline`

### "Error: [accounts.crypto] is a crypto account but [settings] has no local_timezone"
- **Check:** `tjs run` stops at "Checking the project" (exit 1); the message suggests this machine's zone when it can read one. `grep local_timezone taxjson.toml` finds no active line. A name that is not an IANA zone (`"EST5"`) stops instead with "[settings] local_timezone must be an IANA zone name".
- **Cause:** Coinbase and Kraken stamp every row in UTC, and taxjson dates each row in your own zone. There is no default zone (tax-logic CA-DATE-12): a midnight fill near December 31 could land in the wrong year. The `taxjson init` scaffold has a `[accounts.crypto]` table whether or not you hold coins.
- **Fix:** add `local_timezone = "America/Toronto"` (your IANA zone) under `[settings]`. If you hold no crypto, delete the `[accounts.crypto]` table instead. `tjs format` and `tjs migrate` still run without the key.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_normalize_settings`, `but [settings] has no local_timezone`; `src/taxjson/lib/brokerages/_crypto_common.py` — `missing_timezone_message`; `src/taxjson/lib/config_check.py` — `must be an IANA zone`

### "Error: 2 ticker.map problem(s)"
- **Check:** each problem follows as `- ticker.map:<line>: …`, e.g. "CRYPTO needs `CRYPTO SYMBOL YAHOO_ID`" or "line has no ticker.map keyword (GLOBAL/TOBASE/…)". `tjs run` stops (exit 1).
- **Cause:** a line that cannot be parsed, a line without its keyword (an old `yf_ticker.map` or `crypto_ticker.map` line pasted in), or contradictory rules (a rename cycle, two targets for one symbol). A dropped rule would change ACB pools and gains, so the whole map is refused.
- **Fix:** fix the named line (`KEYWORD FROM TO`, separated by spaces; notes after `#`), e.g. `CRYPTO QZQC QZQC9999`, or delete it.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `ticker.map problem(s)`; `src/taxjson/lib/ticker_map.py` — `read_side_rules`, `parse_side_line`, `line has no ticker.map keyword`; `src/taxjson/bin/taxjson_ticker_map.py` — `map_file_problems`

### "Error: 1 ticker.map problem(s)" with "ticker.map:3: GLOBAL joins an option contract (QZK250620C00010000.US) with a share listing (QZK.US)" or "… joins two different option contracts"
- **Check:** the named line is a `GLOBAL`, `TOBASE`, `JOURNAL` or undated `RENAME` line with an option symbol (or a future, `F:…`) on one side and a share symbol on the other, or two option symbols whose expiry, right (C/P), strike or market (`.US`, `.TO`) differ.
- **Cause:** such a line makes the two the same security at every date: the contract's cost was pooled with the shares (or with another contract), and the gains changed without a word. Earlier the line was accepted silently (only a dated `RENAME` was refused).
- **Fix:** delete the line. An option follows its underlying's line: join the shares' symbols instead (a line for the shares moves their options too). A respelling of one contract — the same expiry, right, strike and market, the root spelled otherwise (`GLOBAL QZB.B250620C00010000.TO QZB250620C00010000.TO`, an adjusted-series digit) — is allowed. Book an exercise or assignment as the broker's rows show it.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/bin/taxjson_convert_tt.py` — `_join_derivative_check`; `src/taxjson/bin/taxjson_ticker_map.py` — `_parse_map_text`

### `tjs format-map`: "Warning: 2 ticker.map problem(s): `taxjson run` refuses the map until each is fixed" or "Error: ticker.map: laying the map out in groups would change what it means"
- **Check:** the warning lists each problem as `- ticker.map:<line>: …` (the line numbers of the file before formatting) and says how many lines went to the "Unrecognized" group; the error writes nothing (exit 2).
- **Cause:** a line `taxjson run` cannot use (no keyword, malformed, a second target for one symbol) is kept exactly as written at the end of the file, in the "Unrecognized" group, so the line that wins stays first. The error means that moving the lines into their groups would change which of two contradicting lines wins, or the order of two dated renames that contradict each other.
- **Fix:** fix or delete each named line (see "Error: 2 ticker.map problem(s)" above), then `tjs format-map --write` again.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/ticker_map_format.py` — `format_map`, `would change what it means`; `src/taxjson/bin/taxjson_run.py` — `cmd_format_map`

### "Warning: [settings] option_grant_timing_since is not set, so grant timing (ITA s.49(1)) starts at the project year (2025)"
- **Check:** shown by `tjs run` in a Canadian project with a taxable non-crypto account on grant timing (the default `option_premium_timing`).
- **Cause:** without the key, grant timing starts at `year`, which moves when you bump `year` next spring: last year's year-straddling written options would go back to close timing and their premium would be taxed twice. See `tjs option-boundary` and `tjs tax-logic`.
- **Fix:** add `option_grant_timing_since = 2025` (the first year you file under grant timing) to `[settings]` once, and keep it unchanged in every later year's project.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_grant_since_warning`, `option_grant_timing_since is not set`

### "Warning: no transaction in any account's books is dated 2021 (the books run 2025-01-10 to 2025-12-31)"
- **Check:** the next line says "Every 2021 filing total will be 0. Is [settings] year in taxjson.toml right?"; the run itself finishes.
- **Cause:** `[settings] year` is a plausible year but none of your exports or `.tt` lines fall in it, usually a typo or a project copied from another year without changing `year`.
- **Fix:** set `year` to the tax year your exports cover (and keep `option_grant_timing_since` as it was; see its entry above), then `tjs run`.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_warn_year_without_activity`, `no transaction in any account's books is dated`

### "Error: the canada estimate needs a province"
- **Check:** `tjs estimate` stops before printing anything; `tjs amt` says "could not compute the estimate it builds on" with the same line. The next line says "Pass --province ON|BC|AB or set `province` under [settings] in taxjson.toml." A province that is set but not supported gives "Error: unsupported province 'QC' for the estimate".
- **Cause:** the estimate needs provincial brackets, and taxjson never assumes a province. Only ON, BC and AB are supported.
- **Fix:** `province = "ON"` (or BC, AB) under `[settings]`, or `tjs estimate --province ON`.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_summary`, `_tax_estimate_result`, `the canada estimate needs a province`

### A traceback ending "AttributeError: 'NoneType' object has no attribute 'write'" after `tjs sum >&-`, or `tjs run --no-input 2>&-` stops with exit 1 and no output
- **Check:** the command was started with its output or error stream closed (`>&-`, `2>&-`, or a scheduler that starts it without one); the same command with `>/dev/null` or `2>/dev/null` works.
- **Cause:** since the blank-line handling between console messages (after v0.22.0), every command wraps its output streams at start-up, and a stream the process was started without was wrapped too, so the first line written to it failed.
- **Fix:** upgrade; meanwhile redirect to `/dev/null` instead of closing the stream. A closed stream is now left unwrapped, as in v0.22.0, and the command finishes and exits as it would otherwise.
- **Fixed in:** `v0.23.0`
- **Code:** `src/taxjson/lib/out.py` — `settling_streams`; `src/taxjson/lib/cli_diag.py` — `run_top_level`

## Reading the broker files

### "Error: cannot detect broker for inputs/qt/99900001.csv. Check the header first: …"
- **Check:** `taxjson-detect-brokerage inputs/qt/99900001.csv` prints `unknown` and the same advice. `tjs run` prints one `Info: File … → identified as …` line for each file it could route; this file has none.
- **Cause:** the file's content matches no supported export's header (IB, Questrade, Webull, RBC Direct, Coinbase, Kraken), no generic mapping sits beside it, and its name has no `cb_`/`kr_` fallback. Usually it comes from a broker taxjson has no parser for, or it is not an activity export.
- **Fix:** download the broker's activity export in its standard CSV layout. For another broker, write a column mapping named `99900001.csv.toml` next to the file (start from `examples/generic_wealthsimple.toml`). Rename a file to `cb_…`/`kr_…` only for a Coinbase or Kraken export whose header is not recognised.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_detect_brokerage.py` — `cannot_detect_message`, `cannot detect broker for`; `src/taxjson/lib/brokerages/detect.py` — `content_matches`; `src/taxjson/bin/taxjson_run.py` — `group_inputs_detailed`

### "Error: cannot detect broker for inputs/qt/99900001.csv" (or "Questrade export is missing required column(s) 'Transaction Date'") for a file whose header looks right
- **Check:** `head -c 8 inputs/qt/99900001.csv | od -c` shows `357 273 277` (a UTF-8 byte-order mark) twice before the first column name.
- **Cause:** the file was saved as "CSV UTF-8" by a tool that adds a byte-order mark in front of one the file already had. One mark was always read; the second stayed glued to the first column name, so neither detection nor the parser found the header.
- **Fix:** upgrade: every leading mark is dropped. On an older install, re-export the file from the broker, or save it once more from a text editor as plain UTF-8.
- **Fixed in:** `v0.23.1`
- **Code:** `src/taxjson/lib/brokerages/base.py` — `decode_broker_text`; `src/taxjson/lib/brokerages/questrade.py` — `_read_qt_rows`

### "Error: cannot detect broker for inputs/qt/activity.csv. Closest: a Questrade header lacking column(s) Account #, Account Type. …"
- **Check:** the `Closest:` part names the export the file nearly matched and the columns its header lacks (`a Webull header (Action Code) lacking …`, `an RBC activity header lacking …` for those brokers).
- **Cause:** the header has lost columns. Common reasons: columns deleted or renamed in a spreadsheet, a re-save, or a custom report instead of the standard activity export. Detection needs every column the parser reads.
- **Fix:** download the export from the broker again and put it in `inputs/<account>/` without opening and saving it in a spreadsheet. If the broker really changed its layout, a generic mapping (`<file>.csv.toml`) reads it in the meantime.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/detect.py` — `a Questrade header lacking column(s)`; `src/taxjson/bin/taxjson_detect_brokerage.py` — `Closest:`

### "Error: both.csv: its content matches 2 broker exports — Questrade (…) and RBC Direct Investing (…); refusing to guess which parser reads it."
- **Check:** the file holds two exports' header rows, often two downloads pasted into one file.
- **Cause:** detection reads the content. A file whose content matches two exports could be read wrongly by either parser, so the run stops.
- **Fix:** keep one export per CSV. A generic mapping beside the file (`both.csv.toml`) also decides it: a mapping always wins over the content.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/detect.py` — `ambiguity_message`, `refusing to guess which parser reads it`

### "Error: …/inputs/qt/99900001.csv is empty — a failed or interrupted download?" or "Error: …/99900001.csv: not UTF-8 text (byte 0xe9 at offset 3) — re-export it, or save it as UTF-8 in your editor"
- **Check:** the file is 0 bytes or blank lines only, or a spreadsheet or editor saved it in another encoding (Windows-1252 "CSV (Comma delimited)").
- **Cause:** taxjson reads UTF-8 (with or without BOM) and UTF-16 (with a BOM) only. An empty file is a failed download; renaming either one never helps.
- **Fix:** download the export again. If you must edit it, save it as "CSV UTF-8".
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `is empty — a failed or interrupted download?`; `src/taxjson/lib/cli_diag.py` — `not_utf8`, `not UTF-8 text`

### "Error: spreadsheet export(s) in inputs/ are NOT read — only .csv and .tt files are, …"
- **Check:** the lines under it list each spreadsheet, e.g. `inputs/qt/activity.xlsx`.
- **Cause:** only `.csv` and `.tt` files are read. A `.xlsx`, `.xls`, `.xlsm`, `.ods` or `.numbers` file in an inputs folder would leave every trade in it out of the books. Before v0.17.0 such a file was skipped with no message.
- **Fix:** convert it (`taxjson-xlsx-to-csv FILE.xlsx -o FILE.csv`, or download the broker's CSV) and move the spreadsheet out of `inputs/`. A spreadsheet next to its own CSV conversion (same name) is only a warning.
- **Fixed in:** `v0.17.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `SPREADSHEET_SUFFIXES`, `spreadsheet export(s) in inputs/ are NOT read`

### "Warning: qt: inputs/qt/99900001.csv parsed to 0 transactions — NONE of its rows are in the books"
- **Check:** the line before it, `Warning: 99900001.csv parsed to 0 transactions (155 bytes input, brokerage=questrade)`, names the parser that read the file. `tjs run --strict` stops on it.
- **Cause:** the header was recognised but no row became a transaction: a header-only export (a date range with no activity), or a file whose rows that parser does not book.
- **Fix:** check the export's date range and download it again, or remove a header-only file from `inputs/<account>/`.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_brokerage.py` — `parsed to 0 transactions`; `src/taxjson/bin/taxjson_run.py` — `_ZERO_TX_RE`

### "Warning: inputs/margin/book.tt:1: a .tt line's total 110.00 is not qty x price + fee = 100.00"
- **Check:** the next lines quote the `.tt` line (`BUYSELL 2025-01-10 10:00:00 QZA.TO 10 CAD 10 110`); `tjs checklist`'s run-clean step lists it ("1 .tt line(s) whose total is not qty x price +/- fee, booked as written"), and `tjs run --strict` stops on it. A sale whose commission exceeds its gross, written with a 0 total, reads "a .tt sale's total is 0 but its commission exceeds its gross".
- **Cause:** a `.tt` BUYSELL line's total is what the engine books — a purchase's cost, a sale's proceeds — not quantity x price. A total more than 1% (at least 0.05) off quantity x price (x the contract size) + fee for a purchase, − fee for a sale, is usually a typo; it is booked as written. Before the fix the warning reached only the `.sum` DIAGNOSTICS, so a wrong cost or proceeds went through with nothing on the console, `--strict` and the checklist saying nothing. A line with no price (0: the total alone states the amount) and a futures line without its size are not compared.
- **Fix:** correct the total (or the quantity or price) and `tjs run`. If the total is right as written (a charge the line does not show), put the difference in the line's fee column so the two agree. An `ACQUIRED` line ("a .tt ACQUIRED line's total 530.00 is not qty x price = 480.00") has no fee column: write the price the message gives (the total divided by the quantity). Earlier the warning on an `ACQUIRED` line quoted the `BUYSELL` it expands to and advised the fee column, and the warning and the `--strict` stop named the file unmasked while the "Reading" step line masked an account-number-like part of the name; all three now name it the same (masked) way.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/tt_totals.py` — `read_diag`, `project_mismatches`, `tolerance`, `source_line`, `a .tt line's total`, `ACQUIRED has no fee column`; `src/taxjson/bin/taxjson_convert_tt.py` — `.tt line total`; `src/taxjson/bin/taxjson_run.py` — `_echo_tt_totals`; `src/taxjson/lib/checklist.py` — `d_run_clean`

### Generic importer: "Error: generic_ws.csv: generic importer: no mapping for generic_ws.csv" or "Error: …: generic importer: generic_ws.csv: mapped column(s) not in the CSV header: action -> 'Transaction type'."
- **Check:** a `generic_*.csv` (or a CSV with a `<file>.csv.toml` sidecar) is in `inputs/<account>/`. The second error lists the file's real header after `Header:`.
- **Cause:** the generic importer reads only through a mapping: the CSV's own `<file>.csv.toml`, or the folder's shared `generic.toml` for `generic_*` files. Each `[columns]` value must be a header name exactly as the CSV spells it (case aside).
- **Fix:** copy `examples/generic_wealthsimple.toml` to `inputs/<account>/<file>.csv.toml` and set each `[columns]` entry to your export's header (here `action = "Type"`). Unknown keys, a missing currency and similar mistakes each stop the run with their own message.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/generic.py` — `_load_mapping`, `generic importer: no mapping for`, `not in the CSV header`

### "Warning: UNBOOKED: …", then `tjs run --strict` stops: "Error: --strict: margin: ib input has event(s) the parser could not book — aborting"
- **Check:** each `Warning: UNBOOKED:` line names the file, the row and what was not booked; `work/<account>_<broker>.json.diag` keeps them. Common ones: IB `1 unhandled Corporate Action row(s) in U1234567.csv for: QZQ.` (a delisting, a rights issue), a generic mapping's `action 'REINVEST' (not in [actions]) carries a quantity/amount`, and a Webull `DIV` row (its own entry below).
- **Cause:** a row moves shares or cash, but the parser has no booking for it. The position or the income is wrong until it is entered by hand. `run --strict` refuses to publish while any such line is in a parse.
- **Fix:** book the event by hand in a `.tt` file in the account's folder (BUYSELL, SPLIT or DIVIDEND lines). For a generic mapping, map the action in `[actions]` (or to `"skip"` if it is not an event); that removes the line. A parser's UNBOOKED line stays while the row is in the export, even after the `.tt` booking, so `--strict` keeps stopping on it.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `UNBOOKED_PREFIX`, `unbooked_lines`, `parser could not book — aborting`; `src/taxjson/lib/brokerages/ib_extractor.py` — `unhandled Corporate Action`; `src/taxjson/lib/brokerages/generic.py` — `carries a quantity/amount`

### "Warning: the same broker account (#ab12cd) feeds two taxjson accounts, qt and rrsp" or "Warning: the same export file sits in two accounts: inputs/margin/a.csv, inputs/tfsa/a.csv (identical content)"
- **Check:** look at which `inputs/<account>/` folders hold that broker account's exports (the first message hashes the broker account; the second names both copies).
- **Cause:** one broker account's exports, or one downloaded file, sit in two taxjson accounts, so every row is booked in both.
- **Fix:** keep each broker account's exports under ONE `inputs/<account>/` folder; delete the copy in the wrong folder. `tjs run --strict` stops on either.
- **Fixed in:** `v0.17.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_warn_shared_broker_accounts`, `_duplicate_input_files`, `the same export file sits in two accounts`

### "Warning: Duplicates: a.csv and b.csv both hold 1 identical row(s)" … "Booked ONCE (read as the same row exported twice)", or "Warning: Duplicates: margin_extra.tt line (…) repeats the exported row in ib_2025.csv" … "so BOTH are booked"
- **Check:** the warning names both files and the row; `tjs trades` for that day shows what was booked.
- **Cause:** exports carry no row id. An identical row in two overlapping exports of one account is read as the same trade exported twice, and the run says so when the files' overlap cannot prove it (they share only that row). A hand-kept `.tt` line that repeats an exported trade has no matching id, so both are booked.
- **Fix:** if the two export rows were really two trades, enter the second as a `.tt` line. If the `.tt` line is the exported trade, delete it. Overlapping downloads are otherwise fine to keep.
- **Fixed in:** `v0.17.0`
- **Code:** `src/taxjson/bin/taxjson_sort.py` — `plan_dedup`, `_tt_near_duplicates`, `Booked ONCE (read as the same row exported twice)`

## Missing purchase history and transfers

### "Warning: 2 positions sold in 2025 with no purchase in your files, not in missing_history.json" (or "Info: 1 position(s) go short in margin's data (QZQ.TO)")
- **Check:** `tjs find-missing-history` lists each pair under "AFFECTS 2025" with its first negative date and the sales it touches.
- **Cause:** the exports start after the shares were bought (or the shares were transferred in), so the sale has nothing to close. It is booked as a short and its gain is in no total.
- **Fix:** in this order: add an older export that holds the purchase to `inputs/<account>/`; or enter the purchase as a `.tt` BUYSELL line with its real date and cost (`tjs find-missing-history --write-purchases` drafts these from IB's Basis or a transfer's stated book value); only when the history cannot be recovered, `tjs find-missing-history --write-missing-history` writes missing_history.json, and those sales must then be reported by hand. docs/getting-started.md step 5 walks through it.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/first_run.py` — `render`, `with no purchase in your files, not in `; `src/taxjson/bin/taxjson_run.py` — `_short_positions_note`; `src/taxjson/lib/missing_history.py` — `detect_missing_history`

### "Info: 1 position(s) go short in margin's data (QZD.TO)" or "Warning: 1 position sold in 2025 with no purchase in your files, not in missing_history.json: QZD.TO (margin)" after a Norbert's gambit, with a ticker.map `TOBASE` line (or none) instead of `JOURNAL`
- **Check:** `tjs find-missing-history` lists the CAD line (QZD.TO) short on the gambit's day, and the export shows a buy of one line and a sale of the other that day (RBC trade rows ending `CA JNL`). Look for the journal's evidence: its two transfer legs (RBC `TFR - … TRANSFER TO U$ J~1` / `… FROM C$ J~1`, Questrade's BRW `JOURNAL POSITION` pair) dated the settlement day in `tjs transfers`, or a `.tt` `JOURNAL` line. `tjs sum` has the gambit's gain in its totals.
- **Cause:** the missing-history walks read a day's rows by the broker's clock (RBC numbers a day's rows, newest first), so the sale came before the buy. On a journal's days they read buys first: the journal's legs' own days and, in that account within 5 business days of them, the one nearest day whose trades are the journal's own (its units of the FROM listing bought, the same units of the TO listing sold) — for a journal the books show (a join of the run, a Questrade pair id, RBC's J~ reference on an RBC export, a `.tt` `JOURNAL` line); a legacy ticker.map `JOURNAL` line names every day of a buy of one listing and a sale of the same quantity of the other. Opposite trades of another quantity or direction near a journal are not its own: earlier, any such day within 5 business days of any journal between the two listings was read as one, which hid a sale with no purchase (a 100-unit sale of one listing and a buy of the other two days before an unrelated 50-unit journal). A `TOBASE` line names no journal: it says the two listings are one security, not that units moved between them — read as a journal on every day of its symbols it hid a genuinely missing purchase (a sale of one listing with no purchase and a buy of the other; or, after a gambit, any later sale and rebuy). So a gambit with no legs in the export (a `.tt` project, or a `JOURNAL` line `tjs format-map --write` migrated to `TOBASE`) reads as a short until the journal is declared.
- **Fix:** with the broker's legs in the export nothing is needed. Without them, add the journal as a dated event, the line the run's "Info: 1 day(s) with a buy of one listing and a sale of the other that a ticker.map TOBASE line joins" names, in a `.tt` file of the account (`JOURNAL 2025-05-05 QZD.TO QZD.U.TO 1000`), then `tjs run`. If the units were not journaled, the purchase is missing (the entry above).
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/missing_history.py` — `walk_journal_symbols`, `JournalDays`, `_journal_trade_day`, `_opposite_trades`, `_journal_line_symbols`, `_day_trades`, `detected_journal_symbols`, `journal_leg_key`, `_walk_key`, `undeclared_journal_days`; `src/taxjson/bin/taxjson_run.py` — `_report_short_positions`

### "Info: 1 day(s) with a buy of one listing and a sale of the other that a ticker.map TOBASE line joins, the same quantity, and no journal"
- **Check:** the detail names each day as the `.tt` line to add (`` `JOURNAL 2025-03-03 QZF.TO QZF.U.TO 100` (margin) ``); `tjs find-missing-history` lists the joined symbol short on that day, and `tjs transfers` shows no journal legs for it.
- **Cause:** a buy of one listing and a sale of the same quantity of the other on one day is what a Norbert's gambit looks like, and also what selling shares bought before your files start and buying the other listing looks like. A `TOBASE` line joins the two listings as one security but is no evidence that units moved, so the day reads in the broker's clock order and a sale stamped before the buy has no purchase.
- **Fix:** if the units were journaled, add the named line to a `.tt` file of that account and `tjs run`: the day then reads as the journal's. Otherwise supply the missing purchase (`tjs find-missing-history`).
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/missing_history.py` — `undeclared_journal_days`; `src/taxjson/bin/taxjson_run.py` — `_report_short_positions`, `that a ticker.map TOBASE line `

### "Warning: … with no purchase in your files … Those sales are NOT in `taxjson sum`" while `tjs sum` has the sale in its totals; or "`taxjson sum` books those sales as short sales closed by a later purchase" or "Info: 1 position read short in 2025 by `taxjson find-missing-history`"
- **Check:** `tjs sum --json`: `no_purchase_uncovered` lists the sales the totals lack; `no_purchase_in_totals` lists those the gains engine booked, with `booked` = `short_cover` or `matched`.
- **Cause:** the closing summary and `tjs sum` said "NOT in `taxjson sum`" for every sale the missing-history walk read as sold before bought, also when the gains engine had booked it: as a short sale a later purchase of the year closed (in the totals, at that purchase's cost), or from a purchase it reads first (the walk and the engine order a day's rows differently). Now the claim is made only for sales the gains files lack (an open short at the year's end); a short closed within the year is a Warning saying so, and a sale the engine matched is an Info line.
- **Fix:** for "NOT in `taxjson sum`" and for a short closed by a later purchase when you held the shares before your files start, supply the purchase (`tjs find-missing-history`, docs/getting-started.md step 5). For the Info line, compare the day's trades with the broker's: the totals already have the sale.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/first_run.py` — `engine_booking`, `BOOKED_SHORT_COVER`, `render_blocks`; `src/taxjson/bin/taxjson_run.py` — `_uncovered_sales`, `no_purchase_in_totals`

### "Info: 1 position(s) go short in margin's data (QZN.TO) … Until the purchase is supplied their gain is in no total" for a short sale closed within the year; or `tjs find-missing-history` counting its cover as a sale (InYrSales 2)
- **Check:** the closing summary says "`taxjson sum` books those sales as short sales closed by a later purchase" for the same symbol, and `tjs sum --json` lists it under `no_purchase_in_totals` with `booked` = `short_cover`. `tjs find-missing-history` showed InYrSales 2 and InYrProceeds as the sale's proceeds plus the cover's cost.
- **Cause:** the run's mid-run note said "in no total" for every position that went short, while the gains engine had booked a short a later purchase of the year closed (in the totals, at that purchase's cost). find-missing-history's in-year count took every row that drew on the short, the covering purchase included, and added its cost to the proceeds. After the first fix the note still read the last run's cross-account wash file (`<account>_gains_wash.json`, rebuilt only after the note), so on a re-run after adding (or removing) the covering purchase it said the opposite of the closing summary; it now reads this run's gains files.
- **Fix:** upgrade and `tjs run`. The mid-run note now follows the engine's booking, as the closing summary and `tjs sum` do: "booked as short sales closed by a later purchase" (in the totals), "read short … by the missing-history check" (sold from a purchase in your files), or "in no total" (still short at the year's end). InYrSales and InYrProceeds count sales only; a short carried into the year and covered in it is still listed, with a line saying the cover's gain or loss is in the year. If you held the shares before your files start, supply the purchase (docs/getting-started.md step 5).
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_short_positions_note`, `_report_short_positions`; `src/taxjson/lib/first_run.py` — `engine_booking`; `src/taxjson/lib/missing_history.py` — `assess_tax_year_relevance`; `src/taxjson/bin/taxjson_missing_history.py` — `_print_section`

### "Warning: Short position: QZQ.US (margin): the broker codes the sale on 2025-04-01 CLOSING (IB code C, IB Basis …), but the data holds no position to close"
- **Check:** `tjs find-missing-history` shows the pair with "broker says closing (IB code C)" and IB's Basis for the shares sold.
- **Cause:** IB marks the sale as closing a position, so it is not a short sale: the purchase predates the statements in `inputs/`.
- **Fix:** add the older statement, or `tjs find-missing-history --write-purchases` to draft the purchase line from IB's Basis; fill in the real purchase date and check the cost (IB's Basis is FIFO over IB's lots, not your ACB).
- **Fixed in:** —
- **Code:** `src/taxjson/lib/pipeline.py` — `broker_says_closing`, `CLOSING (IB code C`

### "Warning: Short position: QZQ.TO (tfsa): a registered account (TFSA/RRSP) cannot be short" (or "spot crypto cannot be short")
- **Check:** `tjs find-missing-history`; the pair is listed as in a registered account, or the account is a crypto account.
- **Cause:** a registered account or a coin balance cannot go negative, so the books are missing an acquisition (a transfer in, a deposit, a purchase before the exports). Until it is supplied, later purchases cover the phantom short, so a superficial-loss denial they cause is missed; for crypto no gain is booked.
- **Fix:** add the transfer or purchase rows (an older export, or a `.tt` line), or list the pair with `tjs find-missing-history --write-missing-history`. `tjs run --strict` stops on this.
- **Fixed in:** `v0.17.0`
- **Code:** `src/taxjson/lib/pipeline.py` — `spot crypto cannot be short`, `_book_words`; `src/taxjson/bin/taxjson_run.py` — `go short where`

### "Warning: Transfer-in: margin: 1 transfer-in(s) from outside your books have NO cost in the books (30 QZQ.US (2025-05-01))"
- **Check:** `tjs transfers` lists the row with IN_BOOKS `NO_COST`.
- **Cause:** shares arrived from another broker and the row states no book value (IB's value column is the market value, never your ACB), so they are kept out of the books and a later sale reads as a short.
- **Fix:** add the original purchase as a `.tt` BUYSELL line (date and ACB from the sending broker) dated on or before the transfer; the warning then stops. docs/getting-started.md step 5c.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/transfer_in.py` — `outside your books have NO cost in the books`; `src/taxjson/bin/taxjson_run.py` — `cmd_transfers_view`

### "Warning: Transfer-in: margin: 1 transfer-in(s) from outside your books booked at the ACB the broker states on the row"
- **Check:** `tjs transfers` shows the row with IN_BOOKS `book_value`.
- **Cause:** a Questrade "TRANSFER BOOK VALUE" or RBC "BOOK VALUE" row is booked as an acquisition on its arrival date at that value. It is the sending broker's record, which is not always your ACB (an earlier superficial-loss denial, a return of capital, the same stock in another account). Tax-logic CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV.
- **Fix:** if the figure is right, nothing; it is said every run. To use your own figure, add the original purchase as a `.tt` BUYSELL dated on or before the transfer; the book value is then no longer used. In a US project the holding period starts at arrival unless you enter the original lots.
- **Fixed in:** `v0.18.0`
- **Code:** `src/taxjson/lib/transfer_in.py` — `the broker `, `states on the row`

### A transfer-in from outside your books lost its cost (no "booked at the ACB the broker states" line) or an in-kind move was booked, after a journal between two listings in ANOTHER account
- **Check:** `tjs transfers` shows the arrival in one account and, in another account on or near the same day, a journal's two legs of the same security (a `.tt` `JOURNAL` line's legs, a Questrade BRW `JOURNAL POSITION` pair, RBC `TFR … J~` legs); the arrival's "booked at the ACB the broker states on the row" Warning is missing, or an "in-kind move(s) … booked" Warning names the journal's account.
- **Cause:** the transfer pairing pooled every account's transfer legs per security, so the journal's out-leg of one listing cancelled the other account's arrival (the arrival's book value was dropped and the sale read the other account's cost), or its in-leg paired with a taxable account's transfer-out as an in-kind contribution. A journal moves units inside one account: its legs now cancel within their own pair (or with the broker's leg a one-legged `.tt` line stands beside) and never pair with another account's transfer (tax-logic CA-XLIST-04 / US-XLIST-03).
- **Fix:** re-run `tjs run` on a release with the fix. On an older install remove the `.tt` `JOURNAL` line and add `TOBASE FROM TO` to ticker.map instead.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/transfer_in.py` — `own_journal_legs`, `movable_rows`, `arrivals`, `sidecar_rows`; `src/taxjson/bin/taxjson_run.py` — `transfer_arrivals`, `in_kind_state`, `_sidecar_transfer_rows`

### "Info: 1 position(s) go short in b's data (QZD.U.TO)" after a Norbert's gambit in one account while another account received the same listing by transfer that day; the gambit's join only suggested
- **Check:** `tjs transfers` shows account b's journal legs (RBC `TFR … J~1`, or a Questrade BRW `JOURNAL POSITION` pair) and, the same day, a transfer-in of the same listing (QZD.TO, the same quantity) in account a; `tjs journals` (or `tjs ticker-map --suggest`) lists b's journal as a suggestion, not a join.
- **Cause:** the cross-listing pairing first cancelled every same-symbol out-leg and in-leg across all accounts, before it paired legs by the broker's reference: a's arrival cancelled b's out-leg, so b's journal had one leg left and its two listings were not joined (b's sale of the other listing had no purchase). A Questrade BRW pair in a US project went the same way.
- **Fix:** upgrade and `tjs run`: legs that carry the broker's reference (RBC's `J~`, Questrade's journal pair, in both countries) pair inside their account first, and a journal's leg never cancels another account's transfer (tax-logic CA-XLIST-01 / US-XLIST-01). On an older install add `TOBASE QZD.U.TO QZD.TO` to ticker.map.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/cross_listings.py` — `analyze`

### "Warning: 1 transfer in a taxable loss's 30-day window counted as an account move, not a purchase"
- **Check:** the detail lines name each transfer, its date and the loss sale; `tjs transfers` lists the rows.
- **Cause:** a transfer between your accounts moves shares; it is not an acquisition, so it does not deny the loss (CA-SL-16 / US-WASH-23). If one leg was really a contribution to a registered plan or a purchase, the loss may be superficial (or a wash sale).
- **Fix:** if it was an account move, nothing. A contribution in kind from one of your taxable accounts in the project is not listed here: the run pairs it with the taxable account's transfer-out, books the sale at fair market value and counts the plan's purchase (the "in-kind move(s) … booked" warning; CA-INKIND-04). If it was a contribution from outside the project or a purchase, record it as a BUYSELL dated the day it was acquired (or, for a transfer-out of your taxable account whose plan is not in the project, an `INKIND` line with `plan=`); `[settings] transfers_as_acquisitions = true` counts every transfer.
- **Fixed in:** `v0.22.0`
- **Code:** `src/taxjson/lib/pipeline.py` — `transfer_window_message`, `transfers_as_acquisitions`; `src/taxjson/bin/taxjson_run.py` — `_say_transfer_windows`, `stage_in_kind_context`

### "Warning: 1 in-kind move(s) between your taxable and registered accounts booked at fair market value" (or "… NOT booked", "… NOT booked — ambiguous")
- **Check:** each detail line names the move (`contribution margin → rrsp: 100 QZQ.TO on 2025-03-14`), its value and where the value came from, and the gain or the loss denied; `tjs transfers` labels both rows `in-kind_contribution` / `in-kind_withdrawal` (an ambiguous pair: "ambiguous, NOT booked").
- **Cause:** a taxable account's transfer-out and a registered account's transfer-in of the same security and quantity within 10 days (or the reverse) are an in-kind contribution (withdrawal), not a move of your own. Canada books a contribution as a sale at fair market value (a loss is nil for good, s.40(2)(g)(iv), shown apart from DENIED) and a withdrawal as a purchase at that value (CA-INKIND-01 … CA-INKIND-06). A US contribution is NOT booked: an IRA takes cash only (US-INKIND-01). A move is NOT booked either when no value was found (no `INKIND` line, no market value on the transfer rows, no close from Yahoo), or when it is ambiguous: legs of the same kind (taxable with taxable, registered with registered) pair first across the project as moves of your own, and a taxable leg with a registered one is booked only when neither has another leg of the same security and quantity in the window that could be its partner. Before this, a closer registered leg won over a taxable one, so two moves of your own (one taxable, one registered) could be booked as a contribution and a withdrawal.
- **Fix:** check each value; a value marked ESTIMATED is Yahoo's split-adjusted close. To set your own, add the line the warning prints to a `.tt` file in the taxable account's folder (`INKIND 2025-03-14 QZQ.TO -100 CAD 15.00`; negative = shares out to the plan). For an ambiguous pair, the detail line names the other candidates and both answers: the `INKIND` line with `plan=` books the move (in the folder of the taxable account whose move it was), `INKIND 2025-03-14 QZQ.TO -100 plan=own` keeps that transfer row a move of your own. A US contribution in kind is usually a mistake in the rows (or a rollover between retirement accounts): check `tjs transfers`. `tjs run --strict` stops while a move is not booked.
- **Fixed in:** `v0.23.0`
- **Code:** `src/taxjson/lib/in_kind.py` — `message`, `NOT booked`, `ambiguous`, `pair_all`, `line_legs`, `value`; `src/taxjson/bin/taxjson_run.py` — `_say_in_kind`, `in_kind_state`

### "Warning: 1 transfer row(s) of a taxable account possibly in-kind in parts with a registered account — NOT booked"
- **Check:** each detail line names the taxable transfer row (`margin: 100 QZQ.TO out on 2025-03-14`) and the registered account's rows of the same security the other way in the window (`rrsp received 60 on 2025-03-17, 40 on 2025-03-17`); `tjs transfers` lists them.
- **Cause:** an in-kind move pairs only equal quantities on its own, so a transfer-out that a registered account received in parts (or a withdrawal the taxable account received in parts) stays a transfer: the shares stay in the taxable books and no sale is booked. The run says so instead of guessing (CA-INKIND-01 / US-INKIND-01); `tjs run --strict` stops ("possibly in-kind in parts").
- **Fix:** if the shares went into the plan, add the printed line to a `.tt` file in the taxable account's folder with the fair market value per share: `INKIND 2025-03-14 QZQ.TO -100 CAD 15.00` — the run books the move with the plan's rows that add up to it. If it was a move of your own, add `INKIND 2025-03-14 QZQ.TO -100 plan=own`.
- **Fixed in:** `v0.23.0`
- **Code:** `src/taxjson/lib/in_kind.py` — `in_parts`, `parts_message`, `possibly in-kind in parts`; `src/taxjson/bin/taxjson_run.py` — `_say_in_kind`

### "Error: 1 in-kind move(s) between your taxable and registered accounts cannot be valued: TAXJSON_OFFLINE is set and the close cache has no price for the date"
- **Check:** the detail lines name each move and the `INKIND` line to add; `echo $TAXJSON_OFFLINE` is set.
- **Cause:** neither transfer row states a market value (Questrade, RBC), so the value would be Yahoo's close on the date, and TAXJSON_OFFLINE forbids the lookup with no cached close in `work/.close_cache.json` (CA-INKIND-06 / US-INKIND-03). The run stops rather than book the move at no value.
- **Fix:** add the printed line to a `.tt` file in the taxable account's folder with the fair market value per share (the day's close from the broker's statement or the contribution receipt): `INKIND 2025-03-14 QZQ.TO -100 CAD 15.00`. Or unset TAXJSON_OFFLINE once to look the close up (an estimate, then cached).
- **Fixed in:** `v0.23.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `in_kind_state`, `cannot be valued`; `src/taxjson/lib/price_chain.py` — `close_on`, `OfflineCloseMissing`

### "Warning: 2 positions at a $0 cost (1 sold in 2025, 1 still held)"
- **Check:** `tjs find-missing-history` lists them under "$0-cost corp-action shares".
- **Cause:** a corporate action (a spin-off, a stock dividend, a merger) put shares in the books at no cost, so their sale overstates the gain.
- **Fix:** give the event its value: `tjs elect ACCOUNT --set EVENT_ID=ELECTION --hint fmv_per_share=<value>` for a spin-off or merger, or a `[[distributions]]` entry / `.tt` ADJUST for a stock dividend. docs/getting-started.md step 5d.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/first_run.py` — `zero_cost_sold`, `zero_cost_held`; `src/taxjson/lib/missing_history.py` — `detect_missing_history`

### "Warning: 1 security paid income in 2025 that the books do not hold (a holding with no purchase in your files?)"
- **Check:** `tjs sanity` (and `tjs divs` for the symbol) shows dividends on a symbol with no position.
- **Cause:** the shares were bought before the exports start, or arrived by transfer, and were never sold this year, so nothing goes short; only the income shows they are held.
- **Fix:** add the purchase history as for a missing purchase (an older export or a `.tt` BUYSELL line), or check the dividend's symbol against `ticker.map`.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/first_run.py` — `income_not_held`, `that the books do not hold`

## Holdings

### "Info: 5 accounts with open positions and no holdings file to check them against" (or `tjs sanity`: "Error: no arguments, and no account in taxjson.toml declares `holdings = [...]`")
- **Check:** `tjs checklist` shows step `sanity` as `[m]`.
- **Cause:** nothing compares the books' positions with the broker's own positions report yet.
- **Fix:** export the broker's positions (or write a holdings TOML) and add `holdings = ["~/holdings/margin.toml"]` under `[accounts.margin]`; then `tjs sanity` and `tjs run` check it every time. One-off: `tjs sanity margin=/full/path/positions.toml`.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/first_run.py` — `unchecked_accounts`; `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `no arguments, and no account in `

### `tjs sanity`: "QZD.U.TO MISSING_IN_TAXJSON" (or `QTY_MISMATCH` on QZD.TO) for a listing the run joined by its transfer journal
- **Check:** the run's console said "joined as one security by their transfer journal: QZD.U.TO ↔ QZD.TO …" and `work/ticker.map.effective` has the `TOBASE` / `JOURNAL` line; `ticker.map` itself has no line for the pair. The books hold the position under one symbol, the broker lists it under both.
- **Cause:** `tjs sanity` folded the broker's symbols with `ticker.map` only, not with the listings the run joined itself, so the broker's other-listing position was compared on its own.
- **Fix:** upgrade: sanity folds with the map the books were merged with (`work/ticker.map.effective` when the run wrote one, else `ticker.map`). A real difference still shows.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `_EFF_MAP`; `src/taxjson/lib/cross_listings.py` — `EFFECTIVE_MAP`

### "Warning: 3 positions differ from the broker's holdings files"
- **Check:** `tjs sanity` lists each symbol with TAXJSON and HOLDINGS quantities and an ISSUE: `QTY_MISMATCH`, `MISSING_IN_TAXJSON` (the broker holds it, the books do not), `MISSING_IN_HOLDINGS`.
- **Cause:** fewer shares in taxjson than at the broker usually means missing history (purchases before the exports start, or shares transferred in). A trade after the last export, a symbol spelled differently (`QZQ.TO` vs `QZQ.US`) or a holdings file of another date are the other usual causes.
- **Fix:** `tjs find-missing-history` and `tjs transfers` for missing history; a `ticker.map` line for a spelling difference; re-export when the holdings file is newer than the activity.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_sanity_console`, `MISSING_IN_TAXJSON`; `src/taxjson/lib/positions_check.py`

## Corporate actions and income

### "Error: 1 corp-action event(s) need an election but --no-input" (or "… but stdin is not a TTY"), and `tjs run` exits 3
- **Check:** `tjs elect --pending` lists each event with what each election books and the hints it needs (also in `work/pending_elections.json`).
- **Cause:** a spin-off or merger has more than one tax treatment and only you can choose. The run defers the account until it is chosen; run from a script or an AI assistant, stdin is not a terminal, so it cannot ask.
- **Fix:** `tjs elect margin --set EVENT_ID=ELECTION` (add `--hint KEY=VALUE` where required), or run `tjs run` at a terminal to be asked; then run again.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_corp_actions.py` — `corp-action event(s) need an election `; `src/taxjson/bin/taxjson_run.py` — `cmd_elect`

### "Warning: margin: spin-off SPNC.US on 2025-06-03 (event …) is booked at $0"
- **Check:** `tjs spinoffs` shows the election, the value used and the cost booked.
- **Cause:** the election was saved with `fmv_per_share=0`: no dividend income is booked and the new shares cost $0, so a later sale overstates the gain. The same warning exists for a merger booked at $0 and a spin-off with $0 allocated cost.
- **Fix:** set the value: `tjs elect margin --set EVENT_ID=ELECTION --hint fmv_per_share=<value>` (the line printed with the warning), then `tjs run`.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_warn_zero_value_spinoffs`

### "Warning: QZA.US: stock dividend of 2 share(s) on 2025-07-03 entered at $0 cost"
- **Check:** before it, the parser's line `QZA.US: stock dividend of 2 share(s) on 2025-07-03 (IB Value 80.00 USD) booked as a stock-dividend event` (RBC and Questrade say the same without the IB Value).
- **Cause:** no export carries the declared amount. In Canada the new shares enter the pool at $0 and count as an acquisition (tax-logic CA-STKDIV-01). An IB stock dividend paid in ANOTHER security (another share class) is not booked at all: `Warning: UNBOOKED: … stock dividend on QZA paid 2 share(s) of ANOTHER security, QZC …`.
- **Fix:** add the declared per-share amount as a `[[distributions]]` table in taxjson.toml (`symbol`, `record_date`, `per_share` in the base currency); `tjs run` books it as an ACB increase. That books the cost only: the dividend itself is reported from the T5/T3 slip and is not in taxjson's income totals. For another class, enter the new shares and their cost as a `.tt` BUYSELL.
- **Fixed in:** `v0.17.0`
- **Code:** `src/taxjson/lib/pipeline.py` — `run_gains`, `entered at $0 cost — in Canada it is a dividend at its `; `src/taxjson/lib/brokerages/ib_extractor.py` — `booked as a stock-dividend`, `ANOTHER security`

### "Warning: ticker.map holds 1 JOURNAL line(s) and 1 dated RENAME line(s): dated events written the old way"
- **Check:** `tjs format-map --check` says "ticker.map holds dated events to migrate"; `tjs format-map` (a dry run) shows each `JOURNAL A B` becoming `TOBASE A B` and each dated `RENAME OLD NEW YYYY-MM-DD` moving to `inputs/<account>/renames.tt`.
- **Cause:** ticker.map now holds standing truths only. A journal between two listings and a ticker change happen on a date: they are `.tt` lines of an account, date first (`JOURNAL <date> FROM TO <qty>`, `RENAME <date> OLD NEW`). The old lines still work (a `JOURNAL` line is read as `TOBASE`, a dated `RENAME` as the event), said once per run.
- **Fix:** `tjs format-map --write`: it rewrites the `JOURNAL` lines as `TOBASE`, moves the dated `RENAME` lines (comments too) to a `renames.tt` (the first account whose books carry the change; one per account kind, securities or crypto, whose books hold the symbols), keeps a backup of ticker.map, and refuses rather than change the books. Then `tjs run`: the totals are the same. The tax books never changed with a `JOURNAL` line; the holdings view did (it moves a journal's units only by its rows): where the holdings show the two listings long and short, the dry run names a `.tt` line `JOURNAL <date> FROM TO <qty>` to add.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/dated_events.py` — `legacy_note`, `home_accounts`, `plan_migration`; `src/taxjson/lib/ticker_map_format.py` — `format_map`, `_migrate_items`, `Moved`; `src/taxjson/bin/taxjson_run.py` — `cmd_format_map`, `_journal_gaps`

### "Error: moving ticker.map's dated RENAME lines to .tt files would change the books — nothing was written"
- **Check:** each listed line names the account and the change, how it is booked now and how it would be after the move (`… is booked 2025-04-01 late=separate now and would be 2025-04-01 after the move`), or the contradiction the move would create; `tjs renames` shows the event and its declarations.
- **Cause:** a dated ticker.map `RENAME` and a `.tt` `RENAME` line of the same change say different things (another `late=`, another date). The run books them as one event — the date the earliest declared, each declaring account's `late=` for its own late rows, the map line's `late=` for the others — and moving the map line into an account's `.tt` file would change that (for example an account would then declare both `late=fold` and `late=separate`). Earlier, `--write` moved it anyway and the books changed (a `late=fold` lost, a late buy split from the renamed shares). An account with no books yet (`work/<account>_base.json`) is compared on every change of its kind; earlier such an account of the other kind (a crypto account with no inputs yet, for a securities change) refused every move, and so did a home account whose own `.tt` line chose the other `late=` while another account could take the line — the line now goes to an account whose own lines agree.
- **Fix:** make the two lines say the same, or delete one; run `tjs run`, then `tjs format-map --write` again. A moved line that repeats a `.tt` line written differently but booked the same is moved, with an Info line asking you to keep one.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/dated_events.py` — `plan_migration`, `resolve_renames`, `MigrationPlan`; `src/taxjson/bin/taxjson_run.py` — `cmd_format_map`

### `tjs format-map --write`: "account cc: the ticker change QZOLD.TO -> QZNEW.TO is booked 2025-04-01 late=fold now (ticker.map's late=, the account having no line of its own) and would be 2025-04-01 after the move, wherever the line goes — add `RENAME 2025-04-01 QZOLD.TO QZNEW.TO late=fold` to inputs/cc/renames.tt"
- **Check:** the map's dated `RENAME … late=fold` (or `late=separate`) and a `.tt` line of the same change with the other `late=` in another account; the named account holds the old ticker and has late rows but no line of its own, so it follows the map line's `late=`. On v0.24.0 the refusal named the account without the line to add, and it also refused when another home would have kept every account's choice, or for an account that never held the old ticker.
- **Cause:** once the map line moves into one account's `.tt` file, the change has no event-level `late=` any more (the `.tt` lines disagree), so every other account relying on it would lose it. A home whose own line keeps every account's choice is now taken when there is one (the Info line says "its line keeps every account's late= choice of this change"); an account that did not hold the old ticker before the date never used the event's `late=` and is not compared on it.
- **Fix:** add the line the message gives to the named account's `.tt` file (each account named), `tjs run`, then `tjs format-map --write` again: the books stay the same.
- **Fixed in:** unreleased
- **Code:** `src/taxjson/lib/dated_events.py` — `plan_migration`, `_view`, `_carries`, `wherever the line goes`

### "Error: 1 .tt dated-event line(s) cannot be booked"
- **Check:** each listed line names its file and line (`inputs/<account>/<file>.tt:N`) and the form it expected: `JOURNAL <date> <FROM> <TO> <qty>` or `RENAME <date> <OLD> <NEW> [late=fold|late=separate]`.
- **Cause:** a dated event line is malformed (the ticker.map order `RENAME OLD NEW YYYY-MM-DD` instead of date first, `JOURNAL FROM TO` with no date or quantity, a zero quantity), a JOURNAL or RENAME (or a legacy ticker.map dated `RENAME`) names an option contract or a future ("JOURNAL line names an option contract …: … never journaled", "RENAME line renames an option contract …": a contract never becomes shares by a journal or a ticker change, and an option follows its underlying's rename — write the stock's `RENAME` line), a line is dated in the future ("… in the future — a dated event is written once it has happened") or a JOURNAL moves an implausible quantity, a JOURNAL moves more units than a journal the broker's rows already hold between the same two listings on the line's date ("the broker's rows already hold a journal of 1000 …: … write only the units they lack (500)"), a JOURNAL sits in a crypto account, or RENAME declarations cannot all be true: one ticker renamed to two symbols within a week ("contradicts"), one change declared on two dates further apart ("one change has one date"), a cycle such as `A -> B` plus `B -> A` ("form a cycle"), one account choosing both `late=fold` and `late=separate`, or an undated ticker.map rule for the same ticker. Each declaration counts, `.tt` lines of every account and dated ticker.map lines alike. Nothing is built: a dropped event would change the books. Earlier, two declarations of one change a month apart were booked as two changes, and a swap was accepted.
- **Fix:** fix or delete the line. A ticker change declared in two places is fine when both name the same change within a week (one event, dated the earliest, an Info line); two identical JOURNAL lines are one journal (a Warning: "… repeats … word for word: one journal, booked once"). Two accounts may choose different `late=` for their own late rows. A symbol that really changed back is booked in its account with a `.tt` line `SPLIT <date> <time> OLD NEW 1`.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/bin/taxjson_convert_tt.py` — `parse_journal_line`, `parse_rename_line`, `_rename_derivative_check`, `_not_in_future`; `src/taxjson/lib/dated_events.py` — `read_declarations`, `check_against_map`, `resolve_renames`, `DatedEventError`; `src/taxjson/lib/cross_listings.py` — `declared_twins`, `partial_overlaps`; `src/taxjson/bin/taxjson_run.py` — `_read_dated_events`, `stage_dated_events`

### "Warning: inputs/margin/j.tt:1: JOURNAL 2025-05-05 QZD.TO QZD.U.TO 1500 is booked in full beside the broker's journal of 1000 between the same listings"
- **Check:** the Warning names the broker's journal's legs (`2025-05-06, 2025-05-06`) and, for an RBC gambit, its trades' day; `tjs transfers` shows the broker's legs and the `.tt` line's legs both moving units between the two listings; `tjs journals` lists both.
- **Cause:** a `.tt` `JOURNAL` line that moves more units than the broker's own journal between the same two listings in the same account stops the run on the journal's own date (it would move those units twice), but a line dated one business day off — RBC dates a gambit's trades on the trade day and its `J~` legs on the settlement day — was booked in full on top of the broker's journal without a word.
- **Fix:** if the line restates the broker's journal, date it as the broker's legs (`JOURNAL 2025-05-06 …`): the run then says which units the rows lack, and you write only those. If it is a separate journal, end the line with `separate` (next entry).
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/cross_listings.py` — `near_restatements`, `NEAR_DAYS`; `src/taxjson/bin/taxjson_run.py` — `stage_dated_events`

### The "booked in full beside the broker's journal" Warning stays for a journal that really is separate
- **Check:** the `.tt` `JOURNAL` line is a second, real journal (your records show two gambits a day apart), and the Warning repeats on every run; on v0.24.0 nothing silenced it.
- **Cause:** a bigger `.tt` journal one business day from the broker's journal reads as a likely restatement of it, and the line had no way to say it is not.
- **Fix:** upgrade and end the line with `separate`: `JOURNAL 2025-05-07 QZD.TO QZD.U.TO 1500 separate`. The line is then a journal of its own: booked in full, never settled against the broker's legs near it (a line equal to the broker's journal is booked too, not read as a duplicate) and never said as a restatement. `work/dated_events.state` records it with `"separate": true`.
- **Fixed in:** unreleased
- **Code:** `src/taxjson/bin/taxjson_convert_tt.py` — `parse_journal_line`, `JOURNAL_SEPARATE`; `src/taxjson/lib/dated_events.py` — `Journal`, `settle_journals`; `src/taxjson/lib/cross_listings.py` — `near_restatements`, `partial_overlaps`

### A ticker that changed twice (`RENAME` A to B, then B to C) leaves the position in B and C goes short
- **Check:** `tjs renames` lists both changes, but the second shows no position carried in an account that held A; `tjs shares` shows that account long B and short C.
- **Cause:** the dated renames were applied in the order they were read (ticker.map order, then the accounts' `.tt` files), not by date, and an account counted as holding B only from rows of B, not from the first change's rename row. A `format-map` migration that put the two lines in different accounts' files could reverse the order.
- **Fix:** upgrade: the changes apply in date order, and the first change's rename row counts as holding B (on the same date too, in the lines' order). Re-run `tjs run`.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/renames.py` — `apply_dated_renames`

### "Error: SPLIT QZA.TO→QZB.TO: rename would merge a LONG position into an existing SHORT pool" (US: a total that misses the sales between the declared date and the broker's rename row) with a `.tt` `RENAME … late=fold`
- **Check:** the account's own rows hold the change (a broker corporate-action row or a `.tt` `SPLIT <date> <time> OLD NEW 1`) a few days after the date the `RENAME` line declares, and the account sold OLD between the two dates; `tjs shares` shows NEW short before the rename row.
- **Cause:** `late=fold` re-booked every OLD row on or after the declared date as NEW, even the ones before the account's own rename row, while `tjs renames` called only the rows after that row late. The sale became a short NEW position that the rename row then merged into.
- **Fix:** upgrade: `late=fold` re-books only the rows the engine takes after the account's own rename row (next entry), the rows `tjs renames` lists. Re-run `tjs run`.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/renames.py` — `apply_dated_renames`, `after_rename_row`, `late_rows`

### Canada: an OLD sale on the day of an evening rename row (IB's 20:25 corporate actions, a timed `.tt` SPLIT) goes short with `late=fold` declared, and `run --strict` passes
- **Check:** the account's rename row is timed later in the day (`SPLIT 2025-04-03 20:25:00 QZA.TO QZB.TO 1`) than an OLD trade of the same day (10:00); `work/<account>_base.json` keeps that trade as QZA.TO; `tjs find-missing-history` lists QZA.TO short, and the total misses its gain. A US project books the same input correctly.
- **Cause:** the late rows were decided by clock time, but the Canada engine orders by settlement date and takes a ticker change ahead of every trade executed on its date: the morning sale, kept as OLD, came after the change had emptied OLD.
- **Fix:** upgrade: an account's late rows are the ones its country's engine takes after its own rename row. In Canada every OLD trade executed on the rename row's date is late (`late=fold` books it as NEW; without a declaration `tjs renames` lists it and `run --strict` stops); in the US the clock decides (a trade before the evening row is still OLD). Re-run `tjs run`. A stage tool run on its own (`taxjson-merge2`, `taxjson-ticker-map`) takes `--country` for this and refuses such a row without it.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/renames.py` — `after_rename_row`, `RenameNeedsCountry`, `late_rows`, `apply_dated_renames`; `src/taxjson/lib/corporate_timeline.py` — `event_sort_key`

### A broker's (or a `.tt`) SPLIT QZA→QZB and a `RENAME` QZB→QZC on the same date: "RENAME 2025-04-01 QZB.TO QZC.TO books nothing" and QZC goes short
- **Check:** the account's own rows rename QZA into QZB on the date the `RENAME` line declares for QZB; `tjs renames --pending` lists the line under DECLARED, NOT BOOKED.
- **Cause:** on the date itself only a rename row the run had booked from another declaration counted as holding QZB; the broker's or a `.tt` SPLIT row did not.
- **Fix:** upgrade: any rename row into the old symbol on the date counts, and the second change is stamped no earlier than the first. Re-run `tjs run`.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/renames.py` — `apply_dated_renames`

### "RENAME 2025-04-01 QZA.TO QZB.TO books nothing" when ticker.map respells the rows (`GLOBAL QZAX.TO QZA.TO`), and "is dated, but ticker.map renames QZAX.TO to QZA.TO at every date" for the raw spelling
- **Check:** ticker.map has an undated line mapping the broker's spelling onto the symbol the `RENAME` line names; `tjs renames --pending` lists the line under DECLARED, NOT BOOKED.
- **Cause:** a dated rename books on the exports' raw symbols, before the undated renames apply, and only matched the declared symbol: the rows in the raw spelling were never renamed. Declaring the raw spelling is refused because the map renames it at every date.
- **Fix:** upgrade: a declaration naming the symbol ticker.map gives the rows (`RENAME 2025-04-01 QZA.TO QZB.TO`) books on every raw spelling mapped onto it, late rows included; the refusal of the raw spelling names that line. Re-run `tjs run`.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/renames.py` — `apply_dated_renames`; `src/taxjson/lib/dated_events.py` — `check_against_map`

### "Warning: ATTENTION: 2 trade(s) in QZOLD.TO after its rename to QZNEW.TO on 2025-04-01" for an account that never held QZOLD.TO, while another account's line says `late=fold`
- **Check:** `tjs renames --json` lists the account's rows under `late` as `unresolved`; the account's stage notes (`work/<account>_*.diag`) say "… row(s) of account b on or after 2025-04-01 are kept as QZOLD.TO: late=fold applies to the accounts that held QZOLD.TO before the date".
- **Cause:** an event's `late=` describes how the brokers of the accounts that held the old ticker booked the renamed shares. An account that bought the old ticker only after the change may hold another company's shares now using it; earlier, another account's `late=fold` folded those rows into the new symbol without a word.
- **Fix:** add a line of the account's own to one of its `.tt` files: `RENAME 2025-04-01 QZOLD.TO QZNEW.TO late=separate` (another security) or `… late=fold` (the renamed shares), then `tjs run`.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/renames.py` — `apply_dated_renames`, `DatedRename`, `declared_late`

### "Warning: inputs/margin/m.tt:2: RENAME 2025-04-01 QZAA.TO QZB.TO books nothing"
- **Check:** `tjs renames` lists the line under DECLARED, NOT BOOKED (`--pending` counts it); `work/dated_events.state` records it with status `unused`.
- **Cause:** no account of the line's kind (securities, or crypto for a crypto account's line) holds the old symbol before the date: a typo of the symbol, a date after the last row of it, or a line in an account of the other kind. Earlier such a line was silently ignored. A chain declared for one day (`RENAME 2025-04-01 QZA.TO QZB.TO` and `RENAME 2025-04-01 QZB.TO QZC.TO`) used to book only the first link; it now books both, in date and line order.
- **Fix:** correct the symbols or the date, or delete the line; then `tjs run`. `run --strict` stops on such a line ("--strict: 1 declared rename(s) book nothing — aborting"); earlier it passed.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_warn_unused_renames`; `src/taxjson/lib/renames.py` — `unused_declarations`, `apply_dated_renames`; `src/taxjson/lib/dated_events.py` — `rename_records`

### A `.tt` RENAME in a securities account moved a coin in a crypto account (or the reverse)
- **Check:** `tjs renames` lists the change under the crypto account too, `work/<crypto account>_base.json` holds a SPLIT row for it, and the coin's sale goes short.
- **Cause:** a `.tt` RENAME applied to every account holding the old symbol, whatever its kind; a bare symbol can name a security and a coin.
- **Fix:** upgrade: a `.tt` RENAME applies only to accounts of its declaring account's kind (tax-logic CA-CRYPTO-RENAME / US-CRYPTO-RENAME). Declare a coin's ticker change in a crypto account's `.tt` file. A legacy ticker.map dated RENAME still applies to every account.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/renames.py` — `apply_dated_renames`, `KIND_CRYPTO`; `src/taxjson/lib/dated_events.py` — `account_kind`

### "Error: 1 .tt JOURNAL line(s) join two listings that nothing shows are one security"
- **Check:** each listed line names its place (`inputs/<account>/<file>.tt:N`), the two symbols and why: "their roots differ (QZAAA, QZBBB) and no security name for either listing" (or "the legs' names are not equal word for word"), or "the names name different companies". `tjs journals --pending` lists it as refused with the way out.
- **Cause:** a `.tt` `JOURNAL` line joins its two listings as one security in every account and year, so it needs evidence that they are two listings of ONE security: the same root (the symbol without its venue suffix — a listing suffix of `data/markets.toml` — and, on a Canadian venue, without the US-dollar line's `.U`: `QZG.TO`, `QZG.U.TO`, `QZG.US`; a class, warrant or unit designator is part of the root, so `QZG.A` / `QZG.B` and `QZG.WS` / `QZG.L` differ), or names in the exports that agree as a broker journal's legs' names must, and no names of two different companies (tax-logic CA-XLIST-04 / US-XLIST-03). A typo in a symbol used to merge two companies' pools silently, and any last dotted part used to be read as a venue (two share classes joined on one root).
- **Fix:** check the two symbols. If they are one security under two tickers, add the line the message prints, `TOBASE FROM TO`, to ticker.map (a deliberate, standing join): the `.tt` line is then booked. Otherwise delete the `.tt` line.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/cross_listings.py` — `declared_verdict`, `listing_root`, `UNPROVEN`, `analyze`; `src/taxjson/bin/taxjson_run.py` — `stage_cross_listings`, `nothing shows are one security`

### `reports/<account>_holdings.toml` shows a long on one listing and an equal short on the other after a Norbert's gambit (ticker.map `JOURNAL` line)
- **Check:** `tjs transfers` shows no journal rows for the gambit's day in that account (the export lacks the journal's two legs); the account's totals in `tjs sum` are right, and `tjs sanity` (which compares the joined security) agrees with the broker.
- **Cause:** a ticker.map `JOURNAL` line used to fold the two listings together in the holdings view. It is now read as `TOBASE` (one security for the cost and the loss rules), and the holdings view moves units between listings only by the journal's transfer legs, which this export does not have.
- **Fix:** add the journal as a dated event, one line in a `.tt` file of the account: `JOURNAL 2025-03-05 QZD.TO QZD.U.TO 1000` (the day, the listing the units left, the listing they reached, the quantity), then `tjs run`. A journal the broker's rows already hold is recognised and not booked twice.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/bin/taxjson_export.py` — `_apply_transfer_evidence`; `src/taxjson/lib/dated_events.py` — `write_sidecars`, `settle_journals`

### "Warning: 2 trade(s) in OLDQZ after its rename to NEWQZ on 2025-03-03"
- **Check:** `tjs renames` lists the dated rename, the position it carried and the late trades as UNRESOLVED.
- **Cause:** after a rename the old ticker is not automatically the same security: the broker may still book the renamed shares under it, or another company may now use the ticker. Until you say which, the rows are a separate security and `tjs run --strict` stops.
- **Fix:** add one line to a `.tt` file of the account, date first: `RENAME 2025-03-03 OLDQZ NEWQZ late=fold` (the broker's late rows are the renamed shares) or `… late=separate` (another security); `tjs renames` prints both. A line's `late=` applies to its own account's late rows and to every account without a line of its own that held the old ticker before the date (another account's late rows stay unresolved until its own line says); an account whose broker booked them differently adds its own line with its own `late=` (no longer refused as a contradiction). Releases before the dated `.tt` events took the line in ticker.map (`RENAME OLDQZ NEWQZ 2025-03-03 late=fold`, still read). A trade in the old ticker later on the change's own day is late too (the change is booked at the start of its day).
- **Fixed in:** `v0.17.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_check_renamed_late`; `src/taxjson/lib/renames.py` — `unresolved_late`, `LATE_FOLD`

### "Warning: Income year: XYZQ.TO: distribution 12.50 CAD paid 2026-01-15 with record date 2025-12-31 is income of 2025"
- **Check:** `tjs divs` for the symbol; compare with the T3.
- **Cause:** a Canadian trust's distribution is income of its record-date year (s.104(13); CA-INC-DATE-TRUST), so a December-record distribution paid in January belongs to the earlier return.
- **Fix:** make sure the earlier year's return carries it (that project counts it only if its exports reach the pay date). If the payer is a corporation, add it to `[settings] corporate_distributions` so it is dated when paid.
- **Fixed in:** `v0.17.0`
- **Code:** `src/taxjson/lib/income_dating.py` — `ATTENTION_INCOME_YEAR`, `_ca_year_warnings`

### "Warning: [[distributions]] SAMPA.TO 2025-06-28: the book already has a return-of-capital ADJUST of -25.00 on SAMPA.TO (dated 2025-06-28)"
- **Check:** the next line says "If both are the same distribution the cost is reduced TWICE." `tjs roc-sum` warns "has an ADJUST in the books AND a [[distributions]] entry".
- **Cause:** the broker export (or a `.tt` ADJUST line) already books the return of capital, and a `[[distributions]]` table in `taxjson.toml` books it again on the same date.
- **Fix:** keep one: delete the `[[distributions]]` entry (or the `.tt` ADJUST), then `tjs run`.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_apply_distributions.py` — `_warn_roc_overlaps`, `reduced TWICE`; `src/taxjson/bin/taxjson_run.py` — `_warn_dist_double_entry`

## Interactive Brokers

### IB: "Warning: U1234567.csv: the statement has no Cash Report" or "Error: U1***.csv: parsed rows do not reconcile with IB's own Cash Report"
- **Check:** the file has no `Cash Report,Header,…` lines (a Flex query or a customised statement). For the error, the next line names the currency and the line, e.g. `USD Dividends: parsed 2.50 vs Cash Report 3.50 (diff -1.00)`.
- **Cause:** taxjson checks the money it parsed against IB's own Cash Report totals, per currency: dividends, payments in lieu, withholding, interest, other fees, commissions and trades. Without the section the check is off, and the run says so. A mismatch means a row was dropped, doubled or mis-signed (often an edited statement), and the parse stops.
- **Fix:** export the Activity Statement with the Cash Report section (Flex: add it to the query). On the error, download the statement again and put it in `inputs/<account>/` unedited.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `no Cash Report — parsed money is NOT reconciled`, `parsed rows do not reconcile with IB's own`

### IB: "Warning: U1234567.csv: the account's IB statements end 2025-12-15, before the end of 2025" or "Warning: IB statements leave 2025-12-16 .. 2025-12-19 uncovered (between … and …)"
- **Check:** the `Statement,Data,Period,…` line of each IB file in `inputs/<account>/`.
- **Cause:** an IB statement holds only its Period. taxjson joins the periods of every statement of one IB account and names the days that none covers. Trades and income on those days are missing from the books.
- **Fix:** download the statement for the missing days (or one Activity Statement for the whole year) and add it. Overlapping statements of one IB account are de-duplicated, so keeping both is fine.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `_warn_coverage_gaps`, `Download the statement that covers the rest of`

### "Warning: U1234567.csv: IB statement spans 2 accounts (U1***, U1***)" or "Warning: 99900001.csv: the export holds rows of 2 Questrade accounts (…)"
- **Check:** the IB file's `Account Information` names more than one account (a consolidated statement); the Questrade file has more than one value in `Account #`.
- **Cause:** every row of a file is booked to the one taxjson account whose folder holds it. That is right only when all the broker accounts are one tax entity (two taxable margin accounts); a TFSA or RRSP in the same file would land in those books. Questrade refuses a file that mixes a registered plan into a taxable account, or the reverse.
- **Fix:** export each registered account on its own into its own `inputs/<account>/`. When every account in the file is yours and taxable together, add `combined_broker_accounts = true` under `[accounts.<name>]`: the warning becomes a one-line note. On a sheltered account the setting holds only when every account is the same plan; an IB statement never says which plan, so there it is refused.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `IB statement spans`, `combined_broker_accounts`; `src/taxjson/lib/brokerages/questrade.py` — `the export holds rows of`; `src/taxjson/lib/brokerages/base.py` — `combined_accounts_note`, `combined_accounts_refusal`

### IB: "Warning: 1 dividend(s) in U1234567.csv are accrued but not yet booked as posted dividends (QZQ pay:2025-12-30 ~2.25 USD)."
- **Check:** the statement's `Change in Dividend Accruals` section has a `Po` row whose pay date is inside the statement period, with no `Re` row and no posted `Dividends` row for it.
- **Cause:** IB posts dividend cash with a lag. Accruals are estimates and are not counted as income, so a statement downloaded before the cash posted lacks that dividend.
- **Fix:** download the statement again once IB has posted the payment, and replace the old file.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `accrued but not yet booked as posted dividends`

### IB: "Warning: IB corporate action taxjson cannot book: 'OLDX(US…) Merged(Acquisition) WITH US… 1 for 2 AND USD 5.00 (…)' on 2025-06-03", then the run stops (exit 3)
- **Check:** the run lists the event as `2025-06-03 UNSUPPORTED corporate action: OLDX.US -> … — taxjson cannot book it` with the `taxjson elect margin --set <event id>=<ignore>` line.
- **Cause:** a merger paying shares and cash, or a merger row of a shape taxjson does not know. Neither the old shares' disposal nor the new position is booked. Before v0.17.0 such a row was skipped with only a `.sum` note, leaving the old shares in the books.
- **Fix:** record the exchange by hand in a `.tt` file (the disposal of the old shares and the purchase of the new ones), then `tjs elect margin --set <event id>=ignore` and run again.
- **Fixed in:** `v0.17.0`
- **Code:** `src/taxjson/lib/corp_actions.py` — `_ib_unsupported_events`, `IB corporate action taxjson cannot book`, `UNSUPPORTED corporate action`

## Questrade, RBC Direct and Webull

### Questrade: "Warning: 99900001.csv line 2: DIV row keeps internal symbol code 'X000123' (…)"
- **Check:** the export lists the security under a code (one letter and digits) instead of a ticker, usually for shares transferred in from another broker. The next line gives the ticker.map line to add.
- **Cause:** taxjson resolves a code from the account's own trades and transfers, a matching transfer-out at another broker in the project, or an exact name match (tax-logic CA-ACB-CODES). Nothing resolved this one, so its rows form a pool of their own: a return of capital on it becomes a gain, and the sale of the real ticker goes short.
- **Fix:** add `GLOBAL X000123.TO SAMPH.TO` (the code's listing, then the real ticker) to `ticker.map`; the warning stops.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/questrade.py` — `_resolve_symbol`, `row keeps internal symbol code`; `src/taxjson/lib/symbol_codes.py` — `codes_note`

### Questrade: "Info: Questrade internal symbol codes resolved (1): D0000001 → QZD.US (name match: 'QZD DEVELOPMENT CORP' …)" for a spun-off warrant, then "Warning: Questrade spinoff chain on 2025-10-27 is booked under Questrade's INTERNAL code D0000001.US, not a ticker"
- **Check:** the code's rows are DIS spinoff rows (+50, a -50 reversal, +50 re-booked) whose descriptions say `WTS` / `WARRANT` except one (`QZD DEVELOPMENT CORP SPINOFF ON 500 SHS FROM SEC# …`), and another account holds the common QZD.US. `tjs shares` shows `D0000001.US` long at no cost and the real warrant ticker (QZDW.US) short after its sale.
- **Cause:** the code was matched by name on its one row worded without the warrant designator, so it resolved to the common stock's ticker, while the corp-action stage ignored that resolution and booked the chain under the code. Two contradictory messages, and the position split; warrants traded under the code would have merged into the common's pool.
- **Fix:** re-run `tjs run` on a release with the fix: a code's designators (warrant, right, unit, preferred, the class letter) are read over all of its descriptions and a listing whose name states none is never the code's (and the other way round); the account's own later trade under the real ticker, described like the code's warrant rows, resolves it (`D0000001 → QZDW.US (the account's own rows of QZDW.US are described …)`), and the spinoff chain books under that ticker. On an older install add `GLOBAL D0000001.US QZDW.US` to `ticker.map`.
- **Fixed in:** unreleased
- **Code:** `src/taxjson/lib/symbol_codes.py` — `code_designators`, `designators_agree`, `resolve`, `the account's own rows of`; `src/taxjson/lib/brokerages/questrade.py` — `scan_code_uses`, `_resolve_symbol`; `src/taxjson/lib/corp_actions.py` — `parse_questrade_corporate_actions`, `booked under Questrade's INTERNAL`

### Questrade: after a currency journal (Norbert's gambit), "Warning: Short position: G000123.US (rrsp)" or `tjs find-missing-history`: "G000123.US rrsp -300 (2025-09-30)"; or "Warning: 99900001.csv line 3: a BRW journal row (…) has no partner"
- **Check:** the export's `Other` rows of an earlier day hold the two BRW legs `<NAME> JOURNAL POSITION TO USD` (-300, CAD) and `<NAME> JOURNAL POSITION FROM CAD BOOK VALUE: $X CNV@ r` (+300, USD), or TO CAD / FROM USD the other way, and the later sale `<NAME> WE ACTED AS AGENT` is under a code (one letter and digits). On a release with the fix the run says instead "Questrade internal symbol codes resolved" with `G000123 → QZD.U.TO (journal in-leg of 300 on …)` and, in a Canadian project, "joined as one security by their transfer journal: QZD.TO ↔ QZD.U.TO (currency journal …)".
- **Cause:** the website export names both legs by the bare symbol. The USD leg was read as a US listing (QZD.US) and the code of the sale, which no trade or transfer of the account names, stayed a security of its own, so the sale of the journaled units read as a short. Now the parser pairs the legs (one account, one day, one name, the same quantity), books the USD leg on the security's US-dollar line (the account's own USD listing of it, else `SYMBOL.U.TO` by the TSX convention in `src/taxjson/data/markets.toml`), books the code under that line, and a Canadian run joins the two lines as a ticker.map `JOURNAL` line would (tax-logic CA-XLIST-03): the units keep the CAD purchase's ACB. A leg whose partner is missing from the export stays a transfer of its own line, said with both row shapes.
- **Fix:** re-run `tjs run` on a release with the fix. On an older install add `EXTRACT <NAME> | USD | QZD.U.TO` and `JOURNAL QZD.U.TO QZD.TO` to `ticker.map`. For a lone leg, re-export the journal's day so both rows are in the file. A short that remains on the CAD line (QZD.TO) is the units' real purchase missing from your files: supply it (`tjs find-missing-history`).
- **Fixed in:** `v0.23.0`
- **Code:** `src/taxjson/lib/brokerages/questrade.py` — `_plan_qt_journals`, `_journal_listing`, `journal_codes`, `has no partner`; `src/taxjson/lib/cross_listings.py` — `analyze`, `currency journal`; `src/taxjson/lib/markets.py` — `usd_unit_listing`

### Questrade, an account with `transfers = true`: "joined as one security by their transfer journal: QZD.TO ↔ QZD.U.TO (transfer 2025-09-25)" for a currency journal, or the journal not joined and the USD sale short
- **Check:** the account's `[accounts.NAME]` has `transfers = true`, and the export holds the BRW pair `<NAME> JOURNAL POSITION TO USD` / `… FROM CAD BOOK VALUE: $X CNV@ r`. With `transfers = false` the same export says "(currency journal 2025-09-25)".
- **Cause:** the parser gives the two legs one pair id (`journal_pair`), but the books dropped it: only the transfer sidecar (`transfers = false`) kept it, so with `transfers = true` the run never saw the currency journal and joined the lines only when the names were equal word for word.
- **Fix:** re-run `tjs run` on a release with the fix: the pair id is kept on the book rows too. On an older install add `JOURNAL QZD.U.TO QZD.TO` to ticker.map.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/core.py` — `journal_pair`; `src/taxjson/lib/brokerages/questrade.py` — `_plan_qt_journals`; `src/taxjson/lib/cross_listings.py` — `gather`

### "Warning: 99900001.csv: Questrade symbol QZOLD.US looks renamed to QZNEW.US" or "Warning: 99900001.csv: RBC symbol QZOLD (USD) looks renamed to QZNEW"
- **Check:** the next lines say the old symbol stops with shares still open and the new one (same description) starts with a sale or goes short; `tjs renames --pending` lists it as suggested with the `.tt` line `RENAME <date> OLD NEW` and the dated ticker.map line that book it. IB says `IB lists one stock (contract id …) under several symbols`; Webull says `… goes short with a SALE on … — likely a ticker change Webull reported without a reorganization row`. `tjs shares` shows the old symbol open and the new one short.
- **Cause:** the broker changed the ticker without a reorganization row. As exported, the old pool is stranded and the new symbol's sale reads as a short, so its gain is in no total.
- **Fix:** if they are one security, add the line the warning gives to a `.tt` file of the account: the ticker change as a dated event, date first (`RENAME 2025-05-10 QZOLD.US QZNEW.US`, the new symbol's first row; use the broker's change date if you know it), see `tjs renames`. The hint stops once a `.tt` RENAME line or a ticker.map rule joins them. Earlier releases suggested a ticker.map line (`GLOBAL QZOLD.US QZNEW.US`, or a dated `RENAME QZOLD.US QZNEW.US 2025-05-10`), still read. IB's one contract id under two symbols is booked without a line (next entry).
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/brokerages/questrade.py` — `looks renamed to`; `src/taxjson/lib/brokerages/rbc_direct.py` — `looks renamed to`; `src/taxjson/lib/brokerages/ib_extractor.py` — `under several symbols`; `src/taxjson/lib/brokerages/webull.py` — `ticker change Webull reported without a`

### "Warning: the account's IB statements: IB lists one stock (contract id …) under several symbols: QZOLD, QZNEW — booked as a ticker change, a dated event (QZOLD.US -> QZNEW.US on 2025-05-12)"
- **Check:** `tjs renames` lists the change with source `IB contract id` and the position and cost it carried. If the two symbols are two different securities, `tjs shares` would show QZOLD's rows merged into QZNEW. A trade in QZOLD after the date is listed by `tjs renames` (another company reusing the ticker, or IB's late rows).
- **Cause:** IB changed the ticker of one contract id without a corporate-action row. One contract id is strong evidence: the parser books the change as a dated rename event (event_source `ib-conid`, at 00:00 on QZNEW's earliest row of any section — a trade, a transfer, a corporate action, a dividend, a return of capital): the position, its cost and acquisition dates carry to the new symbol, no ticker.map line needed. It does so only when that contract id's own rows date the change (QZOLD's last row that moves a position or its cost — a trade, a transfer, a corporate action, a return of capital — on an earlier day; a dividend, withholding or payment in lieu of QZOLD paid after the change is income and does not count), read from every IB account of the project: one decision and one date in all of them; otherwise see the next entry. A `.tt` RENAME line for the change, or a ticker.map rule joining the two in that direction, books it instead (no Warning); a corporate action the parse books as the change (IB's own renaming split, a merger) books it from that row. Before the second pre-release review each IB account decided alone (one account booked the change while another refused it, and that account's hint date could move the first account's change past its sale), and a QZOLD dividend or withholding adjustment after QZNEW's first row refused the change.
- **Fix:** nothing when they are one security (earlier releases asked for the ticker.map line `RENAME QZOLD.US QZNEW.US 2025-05-12`, which still works). If they are two securities, add `DISTINCT QZOLD.US QZNEW.US` to ticker.map; if QZOLD's rows after the date are another company's, add `RENAME 2025-05-12 QZOLD.US QZNEW.US late=separate` to a `.tt` file of the account (`late=fold`: IB's late rows are the renamed shares). Then `tjs run`.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `_warn_stock_aliases`, `_book_conid_renames`, `_merge_seen`, `_CONID_MOVES`, `_ib_account_prescans`, `booked as a ticker change`; `src/taxjson/bin/taxjson_run.py` — `stage_ib_project`; `src/taxjson/bin/taxjson_brokerage.py` — `--project-statements`; `src/taxjson/lib/renames.py` — `row_source`, `SOURCE_IB_CONID`

### `run --fast` keeps an IB ticker change dated from another account's IB statement after that statement is removed
- **Check:** `tjs renames` shows the change of one contract id dated on a row that only the removed statement had; `work/<account>_ib_project.state` is missing (older releases) or still lists the removed statement.
- **Cause:** the state of the project's other IB statements is a dependency of each IB parse, but when the last other account's IB statement went the run deleted the state file instead of rewriting it, so `--fast` saw nothing newer and kept the old parse.
- **Fix:** upgrade: the state is kept (with `"accounts": {}`) and the parse is rebuilt when it changes. On an older install run `tjs run` without `--fast`.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `stage_ib_project`

### "Warning: the account's IB statements: IB lists one stock (contract id …) under several symbols: QZOLD, QZNEW — a ticker change taxjson does not book from the contract id: …"
- **Check:** the warning says why and gives the `.tt` line, e.g. "QZNEW's rows begin on 2025-05-12 (a trade or transfer in account 'ma'), on or before QZOLD's last row that moves its position or cost (2025-05-20, a return of capital row in account 'mb') … `RENAME 2025-05-12 QZOLD.US QZNEW.US`" (the date is QZNEW's earliest row in any IB account; every IB account holding the stock says the same), or "ib.csv lists QZNEW under several contract ids (…): the rows there cannot be told apart". `tjs renames` lists the change as suggested; QZNEW's sale goes short until the change is booked (`run --strict` stops). On v0.24.0 the change was booked on QZNEW's first trade (a split or return of capital of QZNEW before that trade was lost; now it is dated at QZNEW's earliest row of any section) and booked even then: a ticker another company had used dated the change from that company's rows, and an IB split that also renames was booked twice (phantom shares).
- **Cause:** the contract id's rows, in every IB account of the project, cannot date the change: QZNEW's rows (any section) begin on or before QZOLD's last row that moves a position or its cost (a trade, a transfer, a corporate action, a return of capital; a dividend, withholding or payment in lieu does not count) (the same day included, or another company using QZOLD later); or one statement lists the symbol under two contract ids (another company used the ticker). A declaration that renames QZOLD to a third symbol (a `.tt` RENAME line, a ticker.map rule) says "not booked as a ticker change: … and that line decides".
- **Fix:** if they are one security, add the `.tt` line the warning gives to a `.tt` file of the account, with the date of the change if you know it (`late=fold` if QZOLD's rows after the date are the renamed shares, `late=separate` if another company's), then `tjs run`. If they are two securities, add `DISTINCT QZOLD.US QZNEW.US` to ticker.map. For a declaration naming a third symbol, fix the declaration: one ticker change, one line.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `_warn_stock_aliases`, `_conid_seen`, `_conid_verdict`, `_conid_link`, `_declared_clash`, `_account_tt_links`, `_ib_placeholder_ticker`, `does not book from the contract id`

### "Warning: the account's IB statements: IB lists one stock (contract id …) under several symbols: QZOLD, QZNEW (a corporate action of 2025-04-01 names QZNEW and QZOLD together, but the parse books no symbol change from it) — …"
- **Check:** the statement has a Corporate Actions row naming both symbols that the parser does not book (a `CUSIP/ISIN Change` or a name change): the run also says "UNBOOKED: … unhandled Corporate Action row(s) … for: QZOLD". The rest of the line is the contract id's own verdict: booked as a ticker change, or the `.tt` line to add (`RENAME 2025-04-01 QZOLD.US QZNEW.US` when the row itself is the first QZNEW row).
- **Cause:** a corporate action naming both symbols stands the contract-id path down only when the parse books it as the symbol change (a renaming split's SPLIT row from QZOLD to QZNEW; a merger is booked by the corporate-action stage). Before the second pre-release review any such row stood it down with "the change is that row's (booked from it)" while nothing booked the change: QZNEW's sale went short, out of the sum, with no ATTENTION.
- **Fix:** if the contract id booked the change, nothing (the UNBOOKED line still asks you to check the corporate action). Otherwise add the `.tt` line it gives (date: the corporate action's day if that is the change), then `tjs run`.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `_conid_link`, `_book_conid_renames`, `but the parse books no symbol`

### "Error: inputs/mb/renames.tt:1: the ticker change QZOLD.US -> QZNEW.US is declared on 2025-05-14, but the IB statements of account 'ma' already trade or transfer QZNEW on 2025-05-12 …"
- **Check:** IB lists QZOLD and QZNEW under one contract id, and a declared date (a `.tt` RENAME line, a ticker.map dated RENAME) falls after the first QZNEW trade or transfer of the named IB account. The run stops at that account's parse.
- **Cause:** booked on the declared date, the change would move that account's position after its QZNEW rows: its QZNEW sale goes short (a hard error in a Canadian project) or sells nothing (a US project, silently). Before the second pre-release review each IB account dated the change from its own rows, so the date one account's warning gave could be too late for another.
- **Fix:** use the line the message gives, dated on or before that day (QZNEW's earliest row in any IB account), e.g. `RENAME 2025-05-12 QZOLD.US QZNEW.US`, then `tjs run`.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `_check_declared_dates`, `_declared_dates`, `the change cannot be later than that row`

### "Warning: ATTENTION: the account's IB statements: IB lists one stock (contract id …) under several symbols: QZOLD, QZNEW — inputs/margin/renames.tt:1 declares the ticker change the other way round (QZNEW.US -> QZOLD.US) …"
- **Check:** the line says how the contract id's rows run (QZOLD's rows end before QZNEW's begin) and the line to write instead; `run --strict` stops.
- **Cause:** a `.tt` RENAME line (or a ticker.map dated RENAME) declares the change from the newer symbol to the older one while the rows date it the other way. Before the second pre-release review a line in either direction silenced the contract id, and the backwards line moved nothing the right way.
- **Fix:** if QZOLD became QZNEW, write the line as the message gives it (`RENAME 2025-05-12 QZOLD.US QZNEW.US`); if they are two securities, replace it with `DISTINCT QZOLD.US QZNEW.US` in ticker.map. Then `tjs run`.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `_declared_join`, `the ticker change the other way round`

### "Warning: ATTENTION: the account's IB statements: IB lists one stock (contract id …) under several symbols: QZOLD, QZNEW — a ticker change"
- **Check:** the warning names the dated line to add (a statement parsed on its own, or a date missing), e.g. `RENAME 2025-05-12 QZOLD.US QZNEW.US`; `tjs shares` shows QZOLD open and QZNEW short. On v0.22.0 and earlier it suggested an undated `GLOBAL` line, ordered by first trade, and could point the real ticker at IB's temporary symbol (a time stamp YYYYMMDDHHMMSS before the ticker, given around a corporate action): `GLOBAL QZX.US <stamp>QZX.US`. Never add that line.
- **Cause:** IB changed the ticker of one contract id without a corporate-action row, so the parser books each symbol as its own security. A temporary time-stamped symbol listed under the ticker's own contract id is now folded onto the ticker by the parser, with one Info line ("… is IB's temporary symbol for QZX …") and no ticker.map line, unless a ticker.map line names the stamped symbol (any keyword: `GLOBAL`, `RENAME`, `TOBASE`, `DISTINCT`, an `EXTRACT` target, `QUOTE` …): then that line decides, and the Info line says "… but a ticker.map line names <stamp>QZX: its rows keep that symbol and the map line decides".
- **Fix:** if they are one security, add the `.tt` line the warning gives, `RENAME YYYY-MM-DD OLD NEW` (OLD is the symbol whose rows end first, the date the first row of the one that continues; `tjs ticker-map --suggest` lists it), then `tjs run`. Remove a `GLOBAL` line that maps a ticker onto a time-stamped symbol.
- **Fixed in:** `v0.23.0`
- **Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `_warn_stock_aliases`, `under several symbols`, `ib_temp_symbol_ticker`, `_ib_temp_folds`, `_map_names_temp`, `_project_map_names`, `_ib_fold_rows`, `is IB's temporary symbol for`, `the map line decides`; `src/taxjson/lib/brokerages/base.py` — `ticker_map_mentioned`; `src/taxjson/lib/ticker_map_suggest.py` — `already`

### `tjs ticker-map --suggest`: "TOBASE QZQ.US QZQ.TO … transfer journal … not joined automatically: the names are not equal word for word (…)"
- **Check:** `tjs ticker-map --suggest` lists the line with its reason; the transfer journal moves one listing of the security out and the other in (a USD line to its CAD line, say). `tjs journals --pending` lists the journal itself (its date, legs and quantity) as suggested, with the same reason.
- **Cause:** taxjson joins two listings of one security on its own only when their security names agree word for word, corporate form and share designators included (tax-logic CA-XLIST-01 / US-XLIST-01); anything less stays a suggestion, so two share classes are never merged. On v0.22.0 and earlier an RBC trade row's confirmation wording ("UNSOLICITED WE ACTED AS PRINCIPAL AVG PRICE …") stayed in the name and "AS" read as a corporate form, so a real pair was only suggested; the next release cuts that wording.
- **Fix:** if both listings are one security, `tjs ticker-map --suggest --write` adds the line (or add it to `ticker.map` by hand), then `tjs run`. If they are different classes or companies, leave it out. Two listings whose names share no leading company word are no longer suggested at all (on v0.22.0 and earlier they were).
- **Fixed in:** `v0.23.0`
- **Code:** `src/taxjson/lib/cross_listings.py` — `_names_verdict`, `the names are not equal word for word `; `src/taxjson/lib/symbol_codes.py` — `_CONFIRM_RE`, `rbc_name`, `questrade_name`; `src/taxjson/lib/ticker_map_suggest.py` — `from_cross_listings`, `not joined `


### `tjs ticker-map --suggest`: "TOBASE QZD.US QZD.TO … not joined automatically: another name of the listings states another share or corporate form (…)" for a broker's own journal (RBC Norbert's gambit, IB InterDepot)
- **Check:** the pair's two legs are one account's, on one day, the same quantity, in the broker's journal wording: RBC `TFR - <NAME> TRANSFER TO C$  J` / `… TRANSFER FROM U$  J~<ref>`, IB `InterDepot (<ROOT>)`, Questrade `<NAME> JOURNAL POSITION TO USD`. The names in the reason are a fund's two brands (`'QZNEWBRAND US DLR CURRENCY ETF UNIT CL A' vs 'QZOLDBRAND U S DLR CURRENCY ETF UNIT NEW …'`), or one name with and one without its corporate form (`'QZNATURAL RESOURCES LTD' vs 'QZNATURAL RESOURCES'`, `'QZGOLD CORP COM NEW' vs 'QZGOLD CORP'`).
- **Cause:** the join compared every name either listing ever had in the project, so a fund renamed after the journal, or a listing another broker spells without LTD, refused a journal whose own two legs name one security.
- **Fix:** upgrade and `tjs run`. An explicit journal pair now compares the two legs' own names on its date; a corporate-form word ending one name only (never a form word inside a name — `QZ SE ASIA FUND` is not `QZ ASIA FUND` — and never `LP`: a partnership is not the company), a leading `THE` and a `COM NEW` spelling do not block it (two stated forms must still agree, every share class and designator counts, and a name naming another company refuses). Each join is a Warning ending "the broker's journal moved the units between the two listings, both legs naming one security (…)"; a `DISTINCT` line undoes it. A ticker.map `TOBASE` / `JOURNAL` line you added for the pair stays valid.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/cross_listings.py` — `journal_wording`, `_explicit_journal`, `_journal_names_verdict`, `_journal_key`, `analyze`, `joined_note`, `the broker's journal moved the units`

### `tjs ticker-map --suggest`: "TOBASE QZD.U.TO QZD.TO … not joined automatically: the legs pair with more than one other leg" (two equal gambits days apart) or "… a listing pairs with two other listings"
- **Check:** `tjs transfers` (or the export) shows two journals of the same quantity a day or two apart (out and in on 2023-03-07, out and in on 2023-03-09), or the security's US-dollar line under two symbols over the years (QZD.US in older exports, QZD.U.TO later), each journaled onto QZD.TO.
- **Cause:** a leg that could pair with two others was ambiguous, even when each journal's two legs were on one day or carried the same broker reference; and a listing joined to two others was ambiguous, even when both were the other-currency lines of one fund moved onto it by the broker's journals.
- **Fix:** upgrade and `tjs run`. Explicit journal legs unique on their day pair first; a reference both legs carry (RBC's `J~…`, Questrade's journal pair) pairs those two and no others; the base-currency listing that several journal pairs map onto is joined to each — when the listings mapped onto it are one security with each other too (each one's name equal to the shared listing's name of its day, or their own names agreeing: a `… PLC` and a `… CORP` that each match a name stating no form stay suggestions, and so do partners whose own listings' names state different corporate forms, an `… LP` and a `… CORP`, even when every journal leg names the shared listing word for word). Ordinary transfers in those shapes stay suggestions: add the `TOBASE` line only if they are one security.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/cross_listings.py` — `analyze`, `_hub_partners_agree`, `the legs pair with more than one other leg`, `a listing pairs with two other listings`

### "Warning: possible superficial loss across listings: QZX.TO sold at a loss, QZX.US bought within 30 days" (US: "possible wash sale across listings")
- **Check:** the message names the loss, the purchase and the name both listings share, then the two ticker.map lines that answer it; `tjs ticker-map --suggest` offers the `TOBASE` line, `tjs scan` lists the pair as XLIST-LOSS, and `tjs run --strict` stops on it. The loss is still allowed in `tjs sum`.
- **Cause:** two listings of one company's shares (the TSX line and the NYSE line) are identical property, but taxjson joins them only on evidence: a ticker.map `TOBASE` line, a `.tt` JOURNAL line or a transfer journal it pairs itself (tax-logic CA-XLIST-01), never on the names alone. Sold at a loss on one listing and bought on the other within 30 days, the unjoined pair kept the loss allowed with nothing said: no suggestion, a clean scan. Now a loss in the tax year with another listing of the same root bought within 30 days before or after it, in any of your accounts (registered included), under a name equal word for word once normalised, is flagged (CA-XLIST-05; US-XLIST-04 for a US project's wash sales). In Canada the other listing must still be held at the end of day 30, as the rule requires; the US rule has no such test. Names that differ, or a listing whose exports carry no name (a `.tt`-only book), are not flagged. A class share's root is also read without its class letter (a loss on `QZT.B.TO` and a purchase of `QZT`, both named "… CL B"); a split or consolidation of the other listing scales the units held at day 30. The run lists the first 20 pairs and counts the rest in one line ("3 more possible superficial losses across listings, not listed here"); `tjs scan` lists every one.
- **Fix:** if the two listings are one security, add the `TOBASE` line (`tjs ticker-map --suggest --write` offers it) and `tjs run`: the loss is denied (disallowed) and its amount goes onto the replacement's cost. If they are different securities (a CDR, another company that uses the root), add the `DISTINCT` line. Either line ends the warning.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/xlist_loss_radar.py` — `analyze`, `open_findings`, `message`, `across listings`, `_roots`, `_held_at`, `RADAR_SHOWN`, `more_message`; `src/taxjson/bin/taxjson_run.py` — `_say_xlist_losses`, `cmd_scan`, `XLIST-LOSS`; `src/taxjson/lib/ticker_map_suggest.py` — `from_xlist_loss_radar`

### `tjs scan`: "MAP-GAP QZE.TO/QZE.US: … QZE.US and QZE.TO share their letters but the names are not equal … — verify" where QZE.TO and QZE.US are two companies that IB lists under one bare symbol
- **Check:** the IB statement's Financial Instrument Information lists two stocks under the symbol QZE (one on the TSX with a CA ISIN, one on the NYSE with a US ISIN); `tjs ticker-map --suggest` or the scan names QZE.US with both companies' names. The books are right: QZE.TO and QZE.US are two securities.
- **Cause:** the IB parser named a stock row from the first instrument the statement lists under its bare symbol, so a USD row of the NYSE company could carry the TSX company's name (and another statement, listing them the other way round, the right one): QZE.US had two names, and the scan could not tell the pair apart.
- **Fix:** upgrade and `tjs run`. When a statement lists several instruments under one symbol, a row takes the name of the one on its own listing's market (the Listing Exch, else the ISIN country), never the first listed; the scan then reads the two as different companies and asks for no line. A `DISTINCT QZE.US QZE.TO` written to quiet it can stay (it changes no figure).
- **Fixed in:** unreleased
- **Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `_security_name`, `fii_all`; `src/taxjson/lib/cross_listings.py` — `gather`, `shown_apart`

### `tjs scan`: "MAP-GAP QZX.TO/QZX.US: both listings appear in this project but ticker.map has no GLOBAL/TOBASE entry" (or "US-LISTING … hold QZX.TO instead") for a CDR or another company that uses the same letters
- **Check:** compare the two listings' names in your broker's exports: a CDR's name says so ("… CDR (CAD HEDGED)"), another company's name shares no company word (a real-estate trust on one venue, a currency ETF on the other). `tjs sum --json` is the same with or without a `DISTINCT` line for the pair.
- **Cause:** the scan read every US and Canadian listing that share a root as a probable interlisting: MAP-GAP asked for a `TOBASE` or `DISTINCT` line, and US-LISTING advised holding the Canadian line, even when the exports showed the Canadian line is a depositary receipt or the names are two companies'. The books were right (two securities); only the scan nagged.
- **Fix:** upgrade. A same-root pair is still a candidate (interlisted shares usually keep their letters), but not when the exports show the two apart: the Canadian line is a depositary receipt (a receipt word such as CDR or ADR in its name, markets.toml `receipt_words`, or a listing on a venue that lists receipts) or the names share no leading company word (the cross-listing join's `companies_differ`). Such a pair needs no `DISTINCT` line. The MAP-GAP message now says what the names show: "QZX.US and QZX.TO carry the same name ('…') but ticker.map does not join them — if they are one security add `TOBASE QZX.US QZX.TO` …; if not, `DISTINCT QZX.US QZX.TO`", or "… names not compared (no security name for QZX.TO) — verify, then …", or "… share their letters but the names are not equal … — verify"; US-LISTING adds "verify QZX.TO is the same security first" unless a ticker.map line or equal names prove it. `reports/crosslistings.rpt` follows the same rule, and `tjs ticker-map --suggest` no longer offers a parser's conditional `TOBASE`/`GLOBAL` hint ("Only if the position is really held under the other listing …") for such a pair. `DISTINCT` lines you wrote stay valid and still silence a pair; one written only to quiet a CDR or another company can go (it changes no figure).
- **Fixed in:** unreleased
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_scan`, `_scan_pair_verdict`, `_scan_listing_names`, `_SCAN_APART`, `carry the same name`, `names not compared`; `src/taxjson/bin/taxjson_lint_crosslistings.py` — `analyze`, `_pair_verdict`, `_listing_names`; `src/taxjson/lib/cross_listings.py` — `shown_apart`, `companies_differ`, `receipt_why`; `src/taxjson/lib/ticker_map_suggest.py` — `pending`, `_export_names`

### "Info: ticker.map:3: `DISTINCT QZX QZX.TO` is read as `DISTINCT QZX.US QZX.TO`" or "Warning: ticker.map:3: `TOBASE QZX QZX.TO` names QZX, a symbol the books do not hold"
- **Check:** the ticker.map line names one listing without its suffix (the broker's bare US ticker) and the other with one. On v0.24.0 and earlier nothing was said, and the `DISTINCT` line did not answer the cross-listing loss warning for QZX.TO and QZX.US (the warning, `tjs scan` XLIST-LOSS and `tjs ticker-map --suggest` kept asking for the pair).
- **Cause:** the books spell every share listing with its suffix: a broker's bare US ticker (a Questrade or RBC USD row, an IB symbol) is `QZX.US`, and a bare symbol is a coin. The loss radar, the suggestions and the cross-listing join compared the line's symbols as written, so `QZX` matched nothing.
- **Fix:** upgrade. A `DISTINCT` line now also keeps the US listing of a bare ticker beside a share listing apart (it changes no symbol, so no pool moves; the pair as written stays, for a coin or a broker's code), with the Info line when the books hold the US listing and not the bare symbol; writing it `DISTINCT QZX.US QZX.TO` says the same. A `GLOBAL`, `TOBASE`, `JOURNAL` or undated `RENAME` line written that way is not re-read (it would join pools and change the gain on its own): write it as the Warning gives it (`TOBASE QZX.US QZX.TO`) and `tjs run`. A line whose bare symbol the books hold (a broker's internal code, a raw spelling the parser keeps) is live and nothing is said.
- **Fixed in:** unreleased
- **Code:** `src/taxjson/bin/taxjson_ticker_map.py` — `distinct_spellings`, `listing_spelling_notes`, `listing_pair_spelling`, `_parse_map_text`; `src/taxjson/lib/journals.py` — `distinct_spellings`; `src/taxjson/bin/taxjson_run.py` — `_say_listing_spellings`

### "Error: 1 .tt JOURNAL line(s) join two listings that nothing shows are one security" — "QZG.NE is written on a venue that lists depositary receipts" or "QZG.TO is named as a depositary receipt (CDR)"
- **Check:** the `.tt` line journals a Canadian depositary receipt (a CDR on Cboe Canada, written `QZG.NE`, or a listing the broker's export names "… CDR") and the US share it holds a fraction of (`QZG.US`). On v0.24.0 and earlier the line was booked: the two share the root QZG, which was taken as evidence that they are one security.
- **Cause:** a CDR is its own security (a receipt over a fraction of the share, usually currency-hedged), not a listing of the share; pooling the two would merge two costs. The books spell a Canadian venue `.TO`, so the venue is read from the line as written; `src/taxjson/data/markets.toml` marks Cboe Canada (`[venues.NE] receipts = true`) and the receipt words (`[lists] receipt_words`).
- **Fix:** a CDR is not journaled into its share: delete the line, and book a sale of one and a purchase of the other if that is what happened. If the two really are one security (both receipts, say), names that agree in the exports let the line book; or join them deliberately with a ticker.map `TOBASE` line.
- **Fixed in:** unreleased
- **Code:** `src/taxjson/lib/cross_listings.py` — `receipt_why`, `declared_verdict`, `_hub_partners_agree`, `is written on a venue that lists depositary receipts`; `src/taxjson/lib/markets.py` — `receipt_suffixes`, `receipt_words`

### A broker journal split over several rows (one reference: 1000 out of QZD.TO, 600 and 400 into QZD.U.TO) is not joined, or its sale reads as missing history
- **Check:** `tjs transfers` shows three or more transfer legs in one account with one broker reference (RBC's `J~…`, Questrade's journal pair): the out-legs on one listing, the in-legs on the other, the same units in total. On v0.24.0 `tjs ticker-map --suggest` or `tjs journals` listed the move (or nothing), and `tjs find-missing-history` could flag the sale after it.
- **Cause:** three readers of a broker reference used three rules: the cross-listing join and the missing-history walk took a reference group as a journal only with exactly one leg each way, while the transfer-in check settled any group whose units balanced.
- **Fix:** upgrade and `tjs run`. One rule now (tax-logic CA-XLIST-04 / US-XLIST-03): a reference group is one journal when its out-legs name one listing, its in-legs one, they move the same units and every leg is within 5 business days of the others. Any other group (units that do not balance, a third listing, legs further apart) is no journal for any of them.
- **Fixed in:** unreleased
- **Code:** `src/taxjson/lib/missing_history.py` — `ref_group_journal`, `_detected_journals`; `src/taxjson/lib/cross_listings.py` — `analyze`; `src/taxjson/lib/transfer_in.py` — `own_journal_legs`

### A move between brokers that changes the listing (RBC `TFO` out of QZP.TO, IB `ATON` into QZP.US) is not joined over a weekend
- **Check:** `tjs transfers` shows the out-leg and the in-leg six or seven calendar days apart with a weekend between them (out on a Wednesday, in the next Tuesday); the new listing's sales read as a short and the old listing's shares never sell.
- **Cause:** the pairing window was 5 calendar days; brokers book a transfer on their business days.
- **Fix:** upgrade and `tjs run`: the window is 5 business days (weekends not counted; holidays count as days). The legs must still be the same quantity and pair uniquely, and the names must be equal. Such a move between two listings that a broker journal already joined is part of that join when its legs' names agree as a journal's must (one broker spelling the corporate form, the other not).
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/cross_listings.py` — `business_days`, `_close`, `PAIR_DAYS`, `a transfer between two listings a broker journal`

### `tjs ticker-map --suggest`: "TOBASE QZLR.US QZLR.TO — rbc.csv: QZLR: dividend row(s) but no trade rows for QZLR in any RBC file of this account" for a US stock
- **Check:** `tjs ticker-map --suggest` lists the line, but no account, `.tt` file or holdings file in the project has the `QZLR.TO` listing (every QZLR row in every broker is in USD). The run's own message ("… booked as QZLR.US, the payment currency's listing. Only if the position is really held under the other listing …") is right to stay.
- **Cause:** a parser hint that says "only if …" (RBC's dividend on a symbol no RBC file trades; RBC's re-described option; IB's currency-tagged symbol; a Questrade code's look-alike) became an unconditional suggestion, so `--suggest --write --all` wrote a line moving a US stock's rows to a TSX listing that does not exist. In the same listing, a suggestion that another suggestion covers (two `EXTRACT` lines for one listing) was headed "Already answered by ticker.map" with no rule in the map.
- **Fix:** upgrade. Such a hint is now suggested only when the project's books hold every symbol the line joins (another account's rows, a `.tt` or `OPENING` line, a holdings file; an option counts for its listing); otherwise `--suggest` shows nothing for it. A covered suggestion is listed under "Covered by another suggestion". If you added the line, remove it from `ticker.map` and run `tjs run`.
- **Fixed in:** `v0.23.0`
- **Code:** `src/taxjson/lib/ticker_map_suggest.py` — `books_symbols`, `pending`, `_conditional`, `covered_by_suggestion`; `src/taxjson/bin/taxjson_run.py` — `cmd_ticker_map`, `Covered by another suggestion`; `src/taxjson/lib/brokerages/rbc_direct.py` — `_report_untraded_income`

### "Warning: QZD.US names two securities: 'QZREALTY TRUST INC' in rrsp at Interactive Brokers; 'SAMPLEX US DLR CURRENCY ETF UNIT' in margin at RBC Direct Investing — add the EXTRACT line"
- **Check:** `tjs ticker-map --suggest` lists `EXTRACT DLR CURRENCY ETF | USD | QZD.U.TO` and `TOBASE QZD.U.TO QZD.TO` (`JOURNAL QZD.U.TO QZD.TO` before the dated `.tt` events). On v0.22.0 and earlier there was no warning; the suggestion was `TOBASE QZD.US QZD.TO` ("another name of the listings states another share or corporate form ('QZREALTY TRUST INC' vs …)"). Never add that line: it merges the fund into the other company's pool.
- **Cause:** RBC (or another broker that writes only the bare symbol) books a TSX fund's US-dollar unit in USD as `ROOT.US`, the same symbol as an NYSE stock of that root held elsewhere: one symbol, two securities, one pool. The fund's real listing is the TSX unit class `ROOT.U.TO` (the convention in `src/taxjson/data/markets.toml`).
- **Fix:** add the suggested `EXTRACT` line (its words match every description of the fund in the project and no other row's), and the `TOBASE` line when a journal moved the units to the CAD line, then `tjs run`; the warning stops. A suggestion with a placeholder (`<words that name it>`) is a template: write the words of the fund's description by hand (they must match the broker's text: `US DLR` and `U S DLR` are different words).
- **Fixed in:** `v0.23.0`
- **Code:** `src/taxjson/lib/cross_listings.py` — `collisions`, `extract_words`, `companies_differ`, `collision_note`, `names two securities`; `src/taxjson/lib/markets.py` — `usd_unit_listing`, `USD_UNITS_RE`; `src/taxjson/lib/ticker_map_suggest.py` — `from_cross_listings`, `clean_extract`; `src/taxjson/bin/taxjson_run.py` — `stage_cross_listings`

### "Warning: rrsp: joined as one security by their transfer journal: QZCJ.US ↔ QZCC.US …" where QZCC.US does not exist (Questrade or RBC files the TSX listing on a USD row)
- **Check:** the in-leg is a Questrade (or RBC) symbol on a USD row whose company trades under another ticker in the US (`tjs transfers` shows QZCJ.US out of IB and QZCC.US into Questrade the same week). The current release says it instead: "QZCJ.US ↔ QZCC.TO (transfer 2025-09-01; QZCC.US read as QZCC.TO: Questrade files the TSX listing on a USD row)", or, for a symbol not in a join, a Warning of its own ending "add `DISTINCT QZCC.US QZCC.TO` to ticker.map".
- **Cause:** Questrade's website export (and RBC) writes a bare ticker and a currency, and the listing suffix came from the currency alone. Interlisted shares that arrived from another broker are filed under the TSX ticker on a USD row, so the transfer-in became a `.US` listing no market has, and the join pooled the company under it (quotes, T1135 domicile, the loss rules). Now the listing is read from the books (tax-logic CA-XLIST-02 / US-XLIST-02): a transfer-in that is the unique arrival, under an equal name, of another ticker's transfer out on the same currency's listing (or the same ticker's other listing), or shares arrived by an unpaired transfer whose `.TO` listing is in the books under an equal name, are booked as `ROOT.TO` in every row of that broker's exports of the account. Not when a ticker.map line names the symbol in any keyword (a lookup line such as `QUOTE` or `T1135` written for `ROOT.US` included), another broker that names its listings trades `ROOT.US`, a rename row joins the tickers, or the account holds `ROOT.TO` in another currency (then `tjs ticker-map --suggest` offers `TOBASE ROOT.US ROOT.TO`).
- **Fix:** upgrade and `tjs run`. If the symbol really is the US listing, add `DISTINCT QZCC.US QZCC.TO` to `ticker.map`. To make a correction explicit, `tjs ticker-map --suggest` lists the equivalent `GLOBAL QZCC.US QZCC.TO` and `TOBASE QZCJ.US QZCC.TO` lines (write both).
- **Fixed in:** `v0.23.0`
- **Code:** `src/taxjson/lib/listing_suffix.py` — `resolve`, `scan_questrade`, `scan_rbc`, `why`, `listing on a`; `src/taxjson/bin/taxjson_run.py` — `stage_listing_suffix`, `_listing_suffix_stale`, `_ticker_map_named`, `stage_cross_listings`; `src/taxjson/bin/taxjson_ticker_map.py` — `named_symbols`, `_lookup_named`; `src/taxjson/bin/taxjson_brokerage.py` — `listing_fixes`; `src/taxjson/lib/cross_listings.py` — `joined_note`

### A correct `TOBASE QZB.US QZA.TO` for a move from IB to Questrade left "Info: 1 position(s) go short in acct's data (QZA.US)" (in a registered account "Warning: Short position: QZA.US …"), or a QZA.US position the broker never held
- **Check:** `tjs transfers` shows QZB.US out of IB and QZA (the company's TSX root) into Questrade on a USD row the same week, under one name; the account also holds QZA.TO in CAD; ticker.map has `TOBASE QZB.US QZA.TO`; `tjs journals` lists the pair as refused by your ticker.map.
- **Cause:** the map's line booked the out-leg as QZA.TO, but the in-leg kept the row currency's listing QZA.US (the account holds QZA.TO in CAD, and one native-currency pool cannot hold both), and the transfer pairing refused the pair because the map names the out-leg. The units left QZA.TO and arrived in QZA.US, a different security: in a registered account a withdrawal at fair value, in a taxable one a later sale read as a short with no purchase (in no total), with nothing on the console. Without the map line the pair was joined as QZB.US and QZA.US.
- **Fix:** upgrade and `tjs run`. The map's renames apply to a transfer's out-leg before the listing and the pairing are read; an out-leg the map books as the in-leg's other listing joins the in-leg to that listing in the base-currency books: "Warning: acct: joined as one security by their transfer journal: QZB.US ↔ QZA.US (transfer 2026-09-01; ticker.map books QZB.US as QZA.TO, so QZA.US is booked as QZA.TO)" (tax-logic CA-XLIST-01 / CA-XLIST-02, US-XLIST-01 / US-XLIST-02). On an older install add `TOBASE QZA.US QZA.TO` to ticker.map (`tjs ticker-map --suggest` offers it).
- **Fixed in:** unreleased
- **Code:** `src/taxjson/lib/listing_suffix.py` — `resolve`, `which ticker.map books as`; `src/taxjson/lib/cross_listings.py` — `analyze`, `map_renames`, `joined_note`; `src/taxjson/bin/taxjson_run.py` — `_ticker_map_renames`, `_listing_suffix_text`, `stage_cross_listings`

### "Warning: acct: ticker.map books the two legs of a transfer as two securities: 24 QZB.US out 2026-09-01 (booked as QZZ.TO) / QZA.US in 2026-09-03"
- **Check:** `tjs journals` lists the pair as refused by your ticker.map; the line naming QZB.US (or QZA.US) books it as a symbol the other leg is not booked as.
- **Cause:** the two legs pair as one move (the same quantity, within 5 business days), but the map's lines book them as two different symbols, so neither leg pairs: the units leave one security and arrive in another (in a registered account a withdrawal at fair value; the in-leg's position has no cost carried). Earlier releases said nothing.
- **Fix:** if they are one security, add the line the Warning names (`TOBASE QZA.US QZZ.TO`) or correct the line naming the other leg; if they are two securities, add `DISTINCT QZB.US QZA.US`. `tjs run --strict` stops until one of them is in ticker.map.
- **Fixed in:** unreleased
- **Code:** `src/taxjson/lib/cross_listings.py` — `map_split`, `map_split_note`, `books the two legs of a transfer as two`; `src/taxjson/bin/taxjson_run.py` — `stage_cross_listings`

### An account number inside a security name, e.g. `tjs ticker-map --suggest` or a join warning naming 'QZX CORP TFR TO 55500001'
- **Check:** the name in the line ends with `TO ACCOUNT <number>`, `FROM ACCOUNT <number>`, `TFR TO <number>`, `TFR FROM <number>` or `TO <number>`, with no `TRANSFER` word before it; the row is an RBC (or Questrade) transfer whose export has no separate security-name column for it.
- **Cause:** the name is read from the row description, and the transfer's account reference was cut only when a `TRANSFER` word came before it, so the account number stayed in the name: in the `.sum`, the console and `--suggest`, and two rows of one security could fail to compare equal.
- **Fix:** upgrade and `tjs run`. The reference is cut with or without a `TRANSFER` word (never a name's first word; a description that is only a reference names nothing).
- **Fixed in:** `v0.23.0`
- **Code:** `src/taxjson/lib/symbol_codes.py` — `_cut_account_ref`, `_ACCOUNT_REF_RE`, `rbc_name`, `questrade_name`; `src/taxjson/lib/listing_suffix.py` — `scan_rbc`

### Questrade or RBC: "Warning: Row check: … BUYSELL QZPIPE.US 2026-09-29: net_amount -44.98 is far from qty*price = 62.90 …" on a dividend reinvestment
- **Check:** the row is a reinvestment (Questrade `REI`, RBC's reinvestment activity) whose description quotes the price in the other currency: `QZPIPE CORP REINV@C$62.90 …` on a USD row, `QZGOLD CORP REINV@U$5.3783 …` on a CAD row. The booked cost (the Net Amount / Value) was right; only the Warning was wrong.
- **Cause:** the parser booked the REINV@ price as the row's price, in the row's currency, so the row check compared units x a Canadian-dollar price against a US-dollar cash amount (or the reverse); RBC's fee, worked out from that price, was nonsense too.
- **Fix:** upgrade. A reinvestment priced in the other currency is booked at the cash per unit (the row's own currency); the description keeps the quoted price. A reinvestment whose price is in the row's currency and misses the cash still warns (and one more than 5% off is refused, naming the row).
- **Fixed in:** unreleased
- **Code:** `src/taxjson/lib/brokerages/rbc_direct.py` — `reinvest_row_price`, `reinvest_identity_error`; `src/taxjson/lib/brokerages/questrade.py` — `reinvest_row_price`; `src/taxjson/lib/brokerages/schema.py` — `is far `

### Questrade: "Warning: Short position: QZP.TO (lira): a registered account (TFSA/RRSP) cannot be short" after a dividend reinvestment (REI) on a USD row
- **Check:** `tjs shares` (or the parsed book) shows the account `+1 QZP.US` and `-1 QZP.TO` for one share: the REI row has the bare TSX ticker (`QZP`) and Currency `USD` (Questrade pays the dividend on the USD side), and a later sale of that share is on a CAD row. On v0.24.0 the parser also said "'QZP' is booked under its own symbol, but the same security … trades as QZP.TO".
- **Cause:** the reinvested share took the row currency's listing (`QZP.US`, which may be another company's NYSE ticker), while the dotted `.QZP` dividend it reinvests bound to the account's held listing `QZP.TO`. Nothing is missing: the purchase sat one listing over.
- **Fix:** upgrade and `tjs run`. A reinvestment of a bare ticker on the other currency's row now books the listing the account trades under the same name and root (its cost stays the row's cash), and an account with no such holding takes the listing another account's evidence proved for the same broker and name (tax-logic CA-XLIST-02 / US-XLIST-02). With neither, the row currency's listing stays (a DRIP of a US stock).
- **Fixed in:** unreleased
- **Code:** `src/taxjson/lib/brokerages/questrade.py` — `_rei_listing`, `_resolve_symbol`; `src/taxjson/lib/listing_suffix.py` — `resolve`, `project_evidence`, `scan_questrade`, `proved`

### Questrade: "Warning: 99900001.csv: 1 dividend(s) marked NON-RES TAX WITHHELD are booked at the NET amount"
- **Check:** the next line lists each dividend (`QZQ.US 2025-03-15 8.50`); `tjs divs` shows it at the net amount with no TAX row. On RBC the same wording is grossed up instead: `tjs events` shows a TAX row described `(Implied Tax)` at 15% of the gross.
- **Cause:** Questrade's export gives neither the gross nor the tax of such a dividend, so it is booked at the net: income understated, foreign tax missing. RBC's export also gives only the net; its parser assumes the 15% US treaty rate (`gross = net / 0.85`) whatever the issuer's country.
- **Fix:** take the gross and the withholding from the T5/NR4 slip. For RBC, compare the implied tax with the slip when the issuer is not American.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/questrade.py` — `net_of_tax`, `marked NON-RES TAX WITHHELD are booked at the NET`; `src/taxjson/lib/brokerages/rbc_direct.py` — `_build_dividend`, `(Implied Tax)`

### RBC: "Warning: 99900001.csv: the account's latest RBC export was taken as of 2025-12-05", or in `reports/<account>.sum` "note: the account's RBC exports that hold 2025 rows were taken by 2026-02-05; RBC posts 2025's year-end book-cost adjustments …"
- **Check:** the export's `Activity Export as of …` line (a file without one is not judged).
- **Cause:** an RBC export holds only what was posted when it was taken. Taken before Dec 31, it lacks the rest of December. RBC posts the year's Dec-31 book-cost adjustments (notional distributions, a year-end return of capital) only the next spring, so an export taken before then, and next year's export starting Jan 1, both lack them.
- **Fix:** export the year again after Jan 31 for the trades, and after the account's posting day for the book-cost rows (`year_end_posting = "06-30"` under `[accounts.<name>]` by default). Keep both files: overlapping downloads of one RBC account are de-duplicated row by row.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/rbc_direct.py` — `rbc_coverage_messages`, `latest RBC export was`, `DEFAULT_YEAR_END_POSTING`, `year-end book-cost adjustments`

### RBC: "Warning: 99900001.csv: line 7: notional distribution 12.34 CAD on XQF.TO raises its ACB"
- **Check:** the RBC row reads `… NOTIONAL DISTRIBUTION ADJUSTMENT TO BOOK COST $12.34`.
- **Cause:** RBC's row carries only the book-cost side. taxjson raises the ACB by the amount (tax-logic CA-DIST-02), but the distribution itself is income on the fund's T3 (usually box 21) and is not in taxjson's income totals.
- **Fix:** take the distribution's income from the T3 slip. The ACB increase needs no action.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/rbc_direct.py` — `_build_book_adjust`, `notional distribution`

### RBC: "Warning: 99900001.csv: reorganization on 2025-11-08: the removal is booked under RBC temporary code A012345 (…) … so it is ASSUMED to be the receipt's ARCN.TO." or "Warning: 99900001.csv: QZT: roc row(s) but no trade rows for QZT in any RBC file of this account"
- **Check:** the project holds only this year's RBC export, and the earlier years come in through a hand-written `.tt` start file.
- **Cause:** the RBC parser learns a security's listing and a temporary code's company from all of the account's RBC exports in the project. Without the earlier exports it guesses: a removal under a temporary code is taken as the receipt's ticker, and income on a symbol no file trades takes the payment currency's listing. A USD return of capital then lands on an empty `.US` pool and becomes a gain.
- **Fix:** keep the earlier years' RBC exports in `inputs/<account>/`, or add the line the warning gives to `ticker.map` (`GLOBAL <old ticker>.TO ARCN.TO`, `TOBASE QZT.US QZT.TO`).
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/rbc_direct.py` — `_emit_stock_reorg`, `booked under RBC temporary code`, `_report_untraded_income`, `in any RBC file of this account`

### Webull: "Warning: UNBOOKED: webull_2025.csv line 8: Webull DIV row QZQ (…) is not booked"
- **Check:** the Webull Trading Summary has a row whose Action Code is not BUY or SELL (DIV, a transfer). `reports/<account>.sum` also says `Webull parser books only BUY/SELL rows — skipped 1 row(s) with other action codes (1× DIV)`.
- **Cause:** the Webull parser books only BUY/SELL rows (option expiries and exercises included). Webull exports no income file, so dividends and interest (the T5 slip) are in no Webull input.
- **Fix:** enter the income from the T5 as `.tt` lines in the account's folder, e.g. `DIVIDEND 2025-03-15 16:00:00 QZQ.US 50 USD 0.24 12.00` and `INTEREST 2025-12-31 16:00:00 USD 12.34`. Enter shares transferred in as a BUYSELL dated on the original purchase, at its cost (a `.tt` TRANSFER is refused in a taxable account). The UNBOOKED line stays while the row is in the export.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/webull.py` — `Webull parser books only BUY/SELL rows`, `is not booked — if it`

### Webull: in `reports/<account>.sum`, "warning: Webull webull_2025.csv: QZQ250620C00020000.US closed at $0 on … — no exercise/assignment charge is configured for this account ([accounts.<name>] exercise_fee), so exercise/assignment was NOT inferred"
- **Check:** the Trading Summary shows the option closed at $0 and a 100-share trade at the strike that settles near the close.
- **Cause:** Webull's export has no exercise or assignment code. The parser pairs the two legs only when the stock trade carries the account's stated exercise/assignment charge. Since v0.18.0 nothing is assumed, so with no `exercise_fee` every candidate is booked as an expiry plus a separate trade, and the premium stays out of the shares' cost or proceeds.
- **Fix:** add `exercise_fee = 1.00` (your broker's charge) under `[accounts.<name>]`. The `.sum` then says `inferred an exercise/assignment …`; check it against the statement. A pair whose quantities differ is still named and must be booked by hand.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/webull.py` — `_mark_assignments`, `exercise_fee`, `no exercise/assignment charge is configured for`

## Crypto

### "Info: 1 crypto send(s) not yet classified as self / gift / payment"
- **Check:** `tjs crypto-sends crypto` lists the send as PENDING, with its fair value and the ready `.tt` line. `tjs run --strict` stops on it ("--strict: crypto: 1 crypto send(s) not yet classified").
- **Cause:** a Coinbase Send or Kraken withdrawal did not arrive in another of your crypto accounts. Only you know whether it went to your own wallet (no tax event) or was a gift or payment (a disposition at fair value).
- **Fix:** `tjs crypto-sends crypto --set ID=self|gift|payment [--note TEXT]` (the ID is in the listing; `--price P` when no price can be found), then `tjs run` (or `--write`) regenerates `inputs/crypto/crypto_sends.tt`. Commit `sends.json`.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_stage_crypto_sends`, `crypto send(s) not yet`; `src/taxjson/lib/crypto_sends.py` — `match_transfers`, `DECISIONS`

### `reports/crypto.sum`: "warning: Kraken trades kraken_demo.csv: the fee currency of 5 fill(s) can't be verified"
- **Check:** the line is in the DIAGNOSTICS block of `reports/crypto.sum` (not on the console). It ends "no Kraken ledgers export (kr_ledgers*.csv) is in the same folder".
- **Cause:** Kraken's trades CSV states every fee in the quote currency, even when Kraken took it in the coin. Only the ledgers export shows which. Without it, a coin fee is booked as coins you never received.
- **Fix:** export the Kraken ledgers for the same dates and put the file (`kr_ledgers*.csv`) in the same `inputs/<account>/` folder as the trades file, then `tjs run`.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/kraken.py` — `_parse_trades`, `can't be verified`

### "Warning: Crypto id: ETH priced from Yahoo ETH-USD at 30 USD on 2025-04-23, but your own ETH rows are priced at …" or "Warning: Crypto id: QZQC has no CRYPTO line in ticker.map, so it is priced as Yahoo QZQC-USD"
- **Check:** the first says "Yahoo ETH-USD looks like another asset"; the second names a numbered id (`QZQC9999-USD`) the price cache holds from earlier runs, and the exact `CRYPTO` line to add. Both are also in the account's `.sum`.
- **Cause:** taxjson carries no built-in coin ids (removed in v0.18.0): a coin is quoted as Yahoo `SYMBOL-USD` unless `ticker.map` maps it. Yahoo gives a ticker that two assets share a number (`SYMBOL<number>-USD`), so the plain id can be a different coin. A project that relied on the old built-in table now needs the line.
- **Fix:** find your coin's id on finance.yahoo.com and add `CRYPTO QZQC QZQC9999` to `ticker.map` (the full pair `QZQC9999-USD` is accepted too); a `CRYPTO` line that names the wrong id is corrected the same way. Then `tjs run`.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/fill_crypto_prices.py` — `ids_seen_in_cache`, `_implausible_yahoo_prices`, `has no CRYPTO line in ticker.map`, `looks like another asset`

### "Error: Unpriced DIVIDEND ETH 2025-04-23: quantity 0.0025 at price 0 and net 0"
- **Check:** the run also prints "Warning: crypto: validation ERROR(s) in the crypto books — numbers may be wrong". `work/crypto_filled.json.diag` (and the `.sum` DIAGNOSTICS) hold "failed to fetch crypto price for ETH …" and "… row(s) left UNPRICED". `tjs run --strict` stops instead.
- **Cause:** online, the Yahoo lookup for the row's coin and date failed (an error, a rate limit, no usable close, or a coin Yahoo lists under another id). The row stays at price 0, which would book $0 income, cost or proceeds. A failed price is never cached.
- **Fix:** re-run `tjs run` when Yahoo is reachable; or add the price to the row; or, when the coin's id is wrong, add a `CRYPTO SYMBOL YAHOO_ID` line to `ticker.map` (the "Crypto id" entry). Before v0.17.0 these rows were booked at $0 with "Validation passed".
- **Fixed in:** `v0.17.0`
- **Code:** `src/taxjson/bin/taxjson_validate.py` — `price lookup failed, so this books`; `src/taxjson/bin/fill_crypto_prices.py` — `left UNPRICED`; `src/taxjson/bin/taxjson_run.py` — `validation ERROR(s) in the crypto`

### "taxjson-fill-crypto: TAXJSON_OFFLINE is set but a crypto price for ETH on 2025-04-23 is not in the cache and would be fetched from Yahoo Finance."
- **Check:** the run stops with "Error: stopped at filling in crypto prices (crypto_sorted.json) (exit 1)". The row is one the export did not price: a Coinbase row with blank price and total, or a Kraken staking reward from an older ledger export without the `amountusd` column.
- **Cause:** an unpriced crypto row takes its fair value from the Yahoo daily close, cached in `~/.crypto_price_cache.json`. `TAXJSON_OFFLINE=1` forbids the download, and that coin and date are not cached.
- **Fix:** run `tjs run` once online (unset `TAXJSON_OFFLINE`); a successful price is cached for later offline runs. Or put the price in the row (a `.tt` line).
- **Fixed in:** —
- **Code:** `src/taxjson/bin/fill_crypto_prices.py` — `get_crypto_price`, `TAXJSON_OFFLINE is set but a`

### "Warning: UNBOOKED: Coinbase coinbase_demo.csv: 1 row(s) of type(s) the parser does not book (Airdrop x1; 2025-05-02..2025-05-02; assets QZQC) move coins"
- **Check:** the next line says "They are NOT in the books: enter each via a .tt file." `tjs run --strict` stops on it.
- **Cause:** the row's Transaction Type is one the Coinbase parser does not know (an airdrop, a new reward label). taxjson does not guess what it means for tax, but the coins moved.
- **Fix:** enter each row as a `.tt` line in the account folder (an airdrop or reward: income and a purchase at fair value; a payment: a sale).
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/coinbase.py` — `the parser does not book`

### "Error: kr_ledgers.csv: Kraken ledger kr_ledgers.csv: refid RZ*** (2025-03-02)" — the leg "is under the books' zero (1e-09 units) but not negligible"
- **Check:** the paragraph names the coin, its tiny amount and its USD value (more than 0.01 USD); the crypto account's files are not booked (exit 1). A dust leg that is worth nothing gives only a note ("dust sweep" / "instant trade … not booked").
- **Cause:** Kraken writes amounts to ten decimals, so a sweep or trade can spend less than 1e-09 of a coin. Such a leg is left out only when it and its share of the other side are each worth at most 0.01 USD (tax-logic CA-CRYPTO-11); one that carries value would be a real sale or purchase lost. Before v0.21.0 any such leg became a 0-unit trade and stopped the account.
- **Fix:** correct the rows in the export, or enter the trade as `.tt` lines and remove its rows from the export.
- **Fixed in:** `v0.21.0`
- **Code:** `src/taxjson/lib/brokerages/kraken.py` — `_check_dust`, `_drop_dust`, `under the books' zero`

## Currency rates

### "Error: no exchange rate for 1 row(s): USD->CAD on 2025-09-25 (BUYSELL QZQ.US; no rates for currency)." (often after "Warning: download failed — Bank of Canada FXUSDCAD …")
- **Check:** before it the run printed "Info: TAXJSON_OFFLINE is set — using cached rates only (no download)", or a "download failed" warning ("Bank of Canada FXUSDCAD", "Bank of Canada noon USDCAD" or "Yahoo Finance USDCAD=X") followed by "Dates it would have covered have no rate this run.", and "Info: FX USD→CAD: Bank of Canada Valet for 0 dates". The run stops at "merging and converting the account's books" (exit 1).
- **Cause:** taxjson converts each row at the rate of its date, or the latest rate of the 5 days before it, and never uses a built-in rate. Offline (`TAXJSON_OFFLINE=1`) with an empty or old rate cache (`~/.currency_price_cache.json`), or with the Bank of Canada Valet API (or the Yahoo fallback before 2017) unreachable (no network, a firewall or proxy, an outage), there is no rate; a failed download is never replaced by another source's rate. The same message for another currency (`EUR->CAD`) means that currency is not in `[settings] source_currencies`, so its rates were never fetched.
- **Fix:** run `tjs run` once online (unset `TAXJSON_OFFLINE`); the rates are cached and later offline runs use them. For a new currency, add it: `source_currencies = ["USD", "EUR"]`. If no source has a rate for that date, enter the row in CAD at that date's rate as a `.tt` line.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_convert_currency.py` — `missing_rate_message`, `no exchange rate for`; `src/taxjson/bin/to_base_curr.py` — `resolve_rows`, `refresh_boc`, `using cached rates only`, `download failed —`; `src/taxjson/bin/taxjson_run.py` — `stage_currency_rates`

## Options

### "Warning: margin: QZQ250117C00040000.US expired 2025-01-17 but the books still hold 1 (long)"
- **Check:** `tjs list margin` shows the contract still open after its expiry.
- **Cause:** the export is missing the expiry, assignment or exercise row. Variants of the warning say the contract was booked under another root (a ticker.map `GLOBAL` line joins them), or that the broker coded the opening trade CLOSING (a missing write or purchase from before the data).
- **Fix:** add the missing row (an expiry is a `.tt` BUYSELL closing the position at 0 on the expiry date), or the ticker.map line the warning prints, and re-run.
- **Fixed in:** `v0.17.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_warn_expired_open_options`, `the export is missing its expiry, `

## Before you file

### `tjs checklist`: "[!] journals … 1 pending journal(s) between two listings (1 suggested, 0 refused) — `taxjson journals --pending`"
- **Check:** `tjs journals --pending` lists each journal the books did not pool: its date, FROM → TO, quantity, broker, how it was found (a Questrade BRW journal, an RBC journal transfer, an IB InterDepot, a move across brokers), the reason, and the lines that settle it. `tjs journals` shows the joined ones too, each with the `TOBASE` / `JOURNAL` line that pools it.
- **Cause:** a broker journal moved a position from one listing of a security to another, and the run did not join the two listings: their names are not equal word for word or the legs pair more than one way (suggested), or the two legs name different companies (refused). Not pooled, the old listing's shares never sell and the new listing's sales read as a short.
- **Fix:** if they are one security, add the `.tt` line (`JOURNAL <date> FROM TO QTY`, in a `.tt` file of the account's `inputs/`) or the ticker.map line it gives, then `tjs run`. If the legs really are two securities, add `DISTINCT FROM TO` to ticker.map: a journal your ticker.map keeps apart (a `DISTINCT` line, a line naming a listing) is still listed as refused, with how to change it, but is a decision made and never pending.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/checklist.py` — `d_journals`; `src/taxjson/lib/journals.py` — `report`, `_classify`; `src/taxjson/lib/cross_listings.py` — `analyze`, `refused`

### `tjs checklist`: "[!] renames … 1 look-alike rename(s) not booked — `taxjson renames --pending`"
- **Check:** `tjs renames --pending` lists each one under SUGGESTED: the old and new symbol, the account, which broker's hint (Questrade, RBC, Webull "looks renamed") and the `.tt` line `RENAME <date> OLD NEW` (then the dated ticker.map line) that books it.
- **Cause:** the broker changed a ticker without a reorganization row: the old symbol stops with shares still open and the new one, under the same security name, starts with a sale. The run's parse warned; nothing books the rename until you say it is one.
- **Fix:** if it is one security, add one of the two lines (the date is the new symbol's first row; the broker's own change date is better if you know it), then `tjs run`. If they are two securities, add `DISTINCT OLD NEW` to ticker.map.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/checklist.py` — `d_renames`; `src/taxjson/lib/renames.py` — `rename_hints`, `_LOOKS_RE`

### "Warning: these books are not the clean result of the current inputs"
- **Check:** `tjs checklist` step `run-clean` names the problem: validation errors in the last run, an account deferred on elections, inputs newer than the books, or a run that did not finish.
- **Cause:** a report command (sum, list, form-export, …) reads the books in `work/`, and those no longer match `inputs/` and taxjson.toml.
- **Fix:** fix what it names and re-run `tjs run` before using any figure.
- **Fixed in:** `v0.17.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_warn_run_state`, `_run_state_problems`

### "Warning: margin: 2 validation ERROR(s) in the merged books — numbers may be wrong"
- **Check:** the DIAGNOSTICS block of `reports/margin.sum`, or `work/margin_validate.diag`, names each row.
- **Cause:** rows the engine cannot book as they are (a negative trade amount, a zero split ratio, an unpriced crypto row). The figures are written anyway.
- **Fix:** fix the named rows (the source file, a `.tt` line, a ticker.map line) and re-run; `tjs run --strict` makes these fatal.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `ERROR(s) in the merged books — numbers may be `; `src/taxjson/bin/taxjson_validate.py`

### "Info: QZQ.TO traded in more than one TAXABLE account": which file do I file from, margin.sum or margin_wash.sum?
- **Check:** `tjs sum` (its FOR THE RETURN block) and `tjs form-export` give the filing figures.
- **Cause:** by design. `<account>.sum` is each account alone; `<account>_wash.sum` is the blended pass (ACB pooled across taxable accounts, s.47, and superficial losses checked against the registered accounts). The pair shows what blending changed.
- **Fix:** file from `<account>_wash.sum`, `tjs sum` or `tjs form-export`, never from `<account>.sum`. Its TOTAL PROCEEDS / TOTAL COST lines are signed engine figures, not Schedule 3 proceeds.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_warn_cross_taxable_overlap`, `stage_wash_pass`

### `tjs wash-sales` shows a loss "permanently denied"
- **Check:** `tjs wash-sales` lists each denied loss, its trigger and DENIED vs ALLOWED; `tjs audit` traces the sale.
- **Cause:** identical property was bought within 30 days of the loss and still held at day 30 in a registered account (or by an affiliated person), so the loss is gone from your return (s.54, s.40(2)(g)(i)). A denial against a taxable repurchase is added to that ACB instead (s.53(1)(f)).
- **Fix:** check each trigger is real (the right account, the same security); a mis-assigned account type or ticker is the usual mistake. The rule ids are in `tjs tax-logic --ids` (CA-SL-*).
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_wash_sales`; `src/taxjson/lib/core.py`

### `tjs checklist`: "[!] inputs-frozen … latest activity 2025-12-31 — January 2026 is not in the books yet"
- **Check:** the step's detail names the latest activity date (IB statements are checked per account).
- **Cause:** a December sale settles in January, and a superficial-loss window runs 30 days past it; both need January of the next year in the exports.
- **Fix:** re-export each account through the end of January of the next year (keep the overlapping files: duplicates are dropped row by row), then `tjs run`.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/checklist.py` — `is not in the books yet`

### `tjs check-dates`: "1 date(s) cannot be right; fix the source file or the parser."
- **Check:** `tjs check-dates` lists each ERROR row (a settlement on a weekend, before the trade, on a closed market day).
- **Cause:** an impossible date moves a sale to the wrong day's rate or the wrong year; usually a hand-typed `.tt` line or an unusual export row.
- **Fix:** correct the date in the `.tt` file; for a broker export row, report it with `tjs redact` output so the parser can be fixed.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_check_dates`; `src/taxjson/lib/check_dates.py`

### `tjs reconcile-slips`: `MISMATCH` or `MISSING_FROM_SLIP` against the T5008 / 1099-B
- **Check:** `tjs reconcile-slips inputs/slips/t5008.csv` lists each symbol with the proceeds and cost difference.
- **Cause:** often legitimate: a broker's book value is not the blended ACB, a corporate action has no slip row, an option written this year and still open is reported without a slip (`NO_SLIP_EXPECTED`). Otherwise a missing export or a symbol mismatch.
- **Fix:** explain each difference (the CRA matches Schedule 3 proceeds to the slips); fix real gaps in the inputs. IB's per-type-code T5008 ("Various") cannot be compared: transcribe a per-security CSV.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_reconcile_slips.py`; `src/taxjson/bin/taxjson_run.py` — `cmd_reconcile_slips`

### `tjs handoff`: "Error: no prior-year record at filed/2024.json"
- **Check:** `tjs checklist` step `handoff` says "no 2024 record".
- **Cause:** handoff compares this year's opening positions with last year's lock, and there is none (first year with taxjson, or `close-year` was never run there).
- **Fix:** run `tjs close-year` in last year's project, then set `[settings] prior_year_record` to that `filed/<year>.json`. In a first year, mark the step done: `tjs checklist --done handoff`.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_handoff`; `src/taxjson/lib/handoff.py`

### "Warning: filed 2024 DRIFTED vs 2024.json"
- **Check:** `tjs check-filed` lists each figure that moved, from the filed value to the new one.
- **Cause:** a code or input change moved a year you already filed (`filed/<year>.json` is the lock `tjs close-year` wrote).
- **Fix:** review the change. Either amend that return, or, if the old figure was wrong and the new one is what you filed, refresh the lock with `tjs close-year --force` in that year's project. `tjs run --strict` stops on drift.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_check_filed_years`, `DRIFTED vs `; `src/taxjson/bin/taxjson_filed.py`
