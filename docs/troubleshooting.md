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

### "Error: `sum` works on one year's project, and this folder holds the year folders 2024, 2025 (with the exports they share)"
- **Check:** `ls` shows `inputs/` and year folders (`2024/`, `2025/`) but no `taxjson.toml`: the folder of exports every year shares (`tjs init`'s layout). `tjs years` lists the years.
- **Cause:** every command but `init`, `years`, `new-year`, `redact`, `tax-logic` and `help` works on one year's project, and this folder holds several; taxjson never guesses which.
- **Fix:** `cd 2025` (or `tjs -C 2025 sum`).
- **Fixed in:** `v0.26.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_refuse_years_root`, `_YEARS_ROOT_CMDS`

### "Error: [settings] inputs_dir = '../../shared' leads to …, outside … (the folder that holds this project)"
- **Check:** the same for `holdings_dir` and `exports_dir`; every command stops at the config check (exit 1). `ls -l` the folder: a symlink pointing further out counts too.
- **Cause:** the shared folders of one folder of exports for every year sit beside the year folders; a path leading outside the folder that holds the project could read or write anywhere.
- **Fix:** keep the shared folder inside the folder holding the year folders (`inputs_dir = "../inputs"`); replace a symlink leading out with the folder itself.
- **Fixed in:** `v0.26.0`
- **Code:** `src/taxjson/lib/project_layout.py` — `folder_setting`, `setting_problems`; `src/taxjson/bin/taxjson_run.py` — `_refuse_folder_settings`

### "Error: [settings] year = 2024 but this folder is 2025: a year folder holds that year's project"
- **Check:** the project reads a shared `inputs_dir` and its folder is named for a year; `grep year 2025/taxjson.toml`.
- **Cause:** with one folder of exports for every year, a year folder's name says which year's project it is; a copied `taxjson.toml` whose `year` was not changed would build the other year's books in this folder.
- **Fix:** set `year` to the folder's year (or move the project to the right folder). `tjs new-year 2026` makes the next year's folder with the year already set.
- **Fixed in:** `v0.26.0`
- **Code:** `src/taxjson/lib/project_layout.py` — `setting_problems`, `a year folder holds that year's project`

### "Error: [settings] exports_dir = '../inputs' overlaps the inputs folder (inputs_dir) (…): the newest year's run replaces files in exports_dir"
- **Check:** `grep _dir 2025/taxjson.toml`: `exports_dir` is (or holds, or sits inside) the inputs folder, the holdings folder, a year folder, the project's `work/`, `reports/` or `filed/`, or the project folder; every command stops at the config check.
- **Cause:** the newest year's run replaces its positions and wash-radar files in `exports_dir`; in a folder that holds anything else it could overwrite an export, a snapshot or a year's results.
- **Fix:** a folder of its own beside the year folders: `exports_dir = "../exports"`. The run removes there only the files its previous export wrote (listed in `exports/.taxjson-exports.json`); anything else in the folder is never touched.
- **Fixed in:** `v0.26.0`
- **Code:** `src/taxjson/lib/project_layout.py` — `exports_overlap`, `setting_problems`; `src/taxjson/bin/taxjson_run.py` — `_write_exports`, `_exports_manifest`, `_EXPORTS_MANIFEST`

### `tjs check-dates` in a year folder: "out-of-range: … — far outside the project year 2024" for every row of a later year
- **Check:** the project reads a shared `inputs_dir`, and the rows are dated in a later year (the newer exports every year shares).
- **Cause:** the check took any row more than a year after the project year for an impossible date; with exports shared by every year, later years' rows are expected.
- **Fix:** upgrade: in a year folder reading shared exports they are one line, "Info: N row(s) dated after 2025: later years' exports in the shared inputs folder, expected", and their dates are checked like the others; a date after today or before 1990 is still an error. A single-folder project still reports them.
- **Fixed in:** `v0.26.0`
- **Code:** `src/taxjson/lib/check_dates.py` — `analyze`, `render`, `later years' exports`

### "Error: [settings] inputs_dir = 'exports' names a folder inside this project" (or "names a folder inside 2024/, another year's project")
- **Check:** `grep _dir taxjson.toml` in the year folder: the setting points into the year folder itself or into another year folder.
- **Cause:** `inputs_dir` names the folder of exports every year shares, beside the year folders; a folder inside one year's project is that year's own, and another year's folder belongs to that year.
- **Fix:** `inputs_dir = "../inputs"` (the folder beside the year folders), or remove the setting to read this project's own `inputs/`. The same for `holdings_dir` pointing into another year's folder (`exports_dir` there is refused as an overlap).
- **Fixed in:** `v0.26.0`
- **Code:** `src/taxjson/lib/project_layout.py` — `folder_setting`, `_year_folders_beside`

### "Warning: no inputs dir for account 'margin' (inputs/margin); skipping" in a year folder, with "Info: ../inputs/margin/ is beside this year folder, but this project reads its own inputs/"
- **Check:** the year folder's `taxjson.toml` has no `inputs_dir`, and `ls ..` shows the shared `inputs/` (a year folder made by hand, or copied from a single-folder project).
- **Cause:** without `inputs_dir` a project reads its own `inputs/`; the exports every year shares are not read.
- **Fix:** add `inputs_dir = "../inputs"` to `[settings]` and `tjs run`. (With the setting, a missing account folder is named by its real path, `../inputs/rrsp`.)
- **Fixed in:** `v0.26.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `stage_account`, `is beside this year folder`

### "Error: ../inputs/crypto/crypto_sends.tt (a .tt file you wrote) and work/crypto_sends/crypto/crypto_sends.tt (generated from sends.json by `taxjson crypto-sends`) have the same name"
- **Check:** `head -1 ../inputs/crypto/crypto_sends.tt` has no "# GENERATED" line; the year generates its own from `sends.json`.
- **Cause:** both files would be read as the account's `crypto_sends` .tt, and only one of them would reach the books.
- **Fix:** rename yours (`crypto_manual.tt`) and `tjs run`.
- **Fixed in:** `v0.26.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_account_tt_files`, `have the same name`

### `tjs align --write --all`: "Warning: 1 ticker.map line(s) of 2024 not brought over: each contradicts this map's lines"
- **Check:** the listed line renames a symbol this year's ticker.map already renames to another target (or closes a rename cycle).
- **Cause:** the two years' maps disagree; bringing the line over would make the map contradict itself, which `tjs run` refuses. The other lines are brought over.
- **Fix:** decide which target is right for this year and edit ticker.map by hand.
- **Fixed in:** `v0.26.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_align`, `not brought over`

### `tjs redact` in a year folder: the copy's taxjson.toml, ticker.map or tobase.map still holds an account id, a comment or an `--also` match, and the console said "taxjson.toml: nothing to redact" (or `tjs redact --check`: "Done. Nothing to redact.")
- **Check:** search the copy (`inputs_redact/taxjson.toml`, `ticker.map`, `tobase.map`) for the values of your accounts' `account`, `broker_accounts` and `query_id` keys and for the `--also` text. On an older release the year's own files were copied as they were.
- **Cause:** in a year folder of a shared-exports project, `taxjson redact` copies the year's own files beside the exports as a runnable project, but it replaced in them only the ids it had found in the exports: an id only the configuration holds, the denylist and `--also` patterns and the contact details in their comments were never applied to them.
- **Fix:** upgrade and run `tjs redact --force` again. The configuration's ids get placeholders (the same as in the exports, so the copy still runs), e-mail addresses and denylist / `--also` matches are replaced in every file, phones, addresses and names in their comments, and `--check` counts them. In a single-folder project an id only taxjson.toml names is now replaced in the exports too. Review the copy before sharing it.
- **Fixed in:** `v0.27.1`
- **Code:** `src/taxjson/bin/taxjson_redact.py` — `redact_project_text`, `config_ids`, `redact_tree`

### `tjs redact`: "Error: inputs/rrsp/questrade_2026.csv: a number would change in the redacted copy — … row 3 of what the parser reads (…): price changed; nothing written"; or, on an older release, `tjs run` on the redacted copy stops with "|Gross Amount| 280.00 is not |Quantity| 2 x Price 1.4 x 1 = 2.80 ('CALL QZP 09/18/26 REDACTED …')"
- **Check:** the error names the file and the row (or the parser's refusal of the copy). On an older release, the redacted copy's option description reads `REDACTED` where the original had the strike and the issuer's name.
- **Cause:** redaction must never change what the parser reads, but a pattern can hit a field it reads: the street-address pattern took a Questrade option description's strike and issuer (`40 QZERO SQUARE`) for a house number and street, and a denylist or `--also` pattern can match part of a number. The copy then booked differently or not at all.
- **Fix:** upgrade: a number right after an option's expiry date is not read as an address, and every redacted export is read by its parser beside the original's text and compared number by number; a difference refuses the whole copy. With the new error, narrow the denylist / `--also` pattern it names; if none is given, report the row's shape (made-up values) as a bug.
- **Fixed in:** `v0.27.1`
- **Code:** `src/taxjson/bin/taxjson_redact.py` — `numbers_changed`, `_sub_address`, `_EXPIRY_BEFORE`

### `tjs redact` in a year folder: "Error: the redacted taxjson.toml would not read as TOML (…) — nothing written"; or, on an older release, a copy (or an `align --write`, `migrate --to-years` result) whose taxjson.toml stops `tjs run` after a `holdings = [` list written over several lines
- **Check:** the project's taxjson.toml has a value over several lines (`holdings = [` with one file per line, a `"""` string). On an older release the written file shows the new first line followed by the old value's remaining lines.
- **Cause:** the editor of taxjson.toml lines (`src/taxjson/lib/project_layout.py` — `set_key_text`: redact's holdings lists and folder settings, `align --write`, `migrate --to-years`) replaced or commented out only the first line of a value.
- **Fix:** upgrade: every line of the value is replaced or commented out, and redact checks that the whole copied file reads before anything is written. With the new message, a denylist / `--also` pattern matched part of a setting: narrow the pattern (or fix the project's own taxjson.toml if `tjs run` refuses it too).
- **Fixed in:** `v0.27.1`
- **Code:** `src/taxjson/lib/project_layout.py` — `set_key_text`, `toml_statements`; `src/taxjson/bin/taxjson_redact.py` — `_copy_holdings_lists`, `_valid_copy_config`

### `tjs new-year 2026`: the new taxjson.toml still lists last year's `holdings = [...]` for an account whose table is quoted or hyphenated (`[accounts."margin-main"]`, `[accounts.margin-main]`)
- **Check:** `grep -n holdings 2026/taxjson.toml` shows a line not commented out under such an account, although new-year said the accounts' holdings were commented out; or that account's other lines were commented out when `[estimate]` or `[instalments]` came just before it.
- **Cause:** new-year recognised only table names made of letters, digits, `_` and `.`: a quoted or hyphenated name was not read as a table, so the table before it stayed in effect.
- **Fix:** upgrade; for a year made by an older release, comment out (or delete) the account's `holdings` line in the new year's taxjson.toml and uncomment any account line commented by mistake (the new year's snapshots go in its holdings/).
- **Fixed in:** `v0.27.1`
- **Code:** `src/taxjson/lib/project_layout.py` — `new_year_text`, `toml_statements`

### "Info: ../inputs/: rrsp2 — not an account of 2024 (no [accounts.NAME] here): not read"
- **Check:** `tjs years`: another year's `taxjson.toml` has `[accounts.rrsp2]` (an account split, opened or closed in another year).
- **Cause:** every year reads the shared `inputs/`, but a year's books hold only the accounts its own `taxjson.toml` declares.
- **Fix:** nothing when the account is not this year's. When it is, add the `[accounts.rrsp2]` table (`tjs align --from 2025` brings it from the year that has it) and `tjs run`.
- **Fixed in:** `v0.26.0`
- **Code:** `src/taxjson/lib/project_layout.py` — `unconfigured_inputs`; `src/taxjson/bin/taxjson_run.py` — `not an account of`

### "Warning: these books are not the clean result of the current inputs - inputs changed since the last full run (added: inputs/margin/late.tt) … 2024 is filed (filed/2024.json) and its inputs changed since the last run"
- **Check:** `tjs years` shows the filed year with "inputs changed since"; the file is a download saved in the shared `inputs/` while working on a later year.
- **Cause:** every year reads the shared exports, so a new or corrected file can change a filed year's figures; its books in `work/` were built before it.
- **Fix:** `tjs run` in the filed year's folder: it recomputes the year and warns `filed 2024 DRIFTED vs 2024.json` when the filed figures moved (then amend the return, or keep the books consistent with it), or says `filed 2024: OK`.
- **Fixed in:** `v0.26.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_filed_year_stale_hint`, `_check_filed_years`; `src/taxjson/lib/checklist.py` — `d_filed_lock`, `inputs_changed`

### "Warning: ../inputs/crypto/crypto_sends.tt: generated by an older taxjson into the exports every year shares — not read"
- **Check:** the file starts with "# GENERATED by `taxjson crypto-sends --write`"; the project reads a shared `inputs_dir`.
- **Cause:** the sales of your crypto-send decisions are generated from `sends.json` and the year's own ticker.map; with exports shared by every year, each year generates them in its own `work/crypto_sends/` so no year's run rewrites another's. The old file in the shared folder would book them twice.
- **Fix:** delete it once no single-folder project still reads that folder; nothing is lost (the decisions are in `sends.json`). A `.tt` file of that name you wrote yourself (no GENERATED line) is read as before.
- **Fixed in:** `v0.26.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_account_tt_files`; `src/taxjson/lib/crypto_sends.py` — `tt_path`, `GENERATED_DIR`

### "Error: missing_history.json is no longer read — missing history is dated .tt lines (OPENING <date> <SYMBOL> <qty> cost=unknown in inputs/<account>/missing_history.tt): run `taxjson migrate` to convert it"
- **Check:** `ls missing_history.json phantoms.json` in the project (or the year folder); every command but `migrate`, `init` and `help` stops with this line (exit 2), and `tjs checklist` shows it as its configure item.
- **Cause:** since v0.27.0 missing history is a dated event in the account's inputs — `OPENING <date> <SYMBOL> <qty> cost=unknown [reason="..."]` beside the exports, its quantity and date fixed (with one folder of exports for every year: one record for every year). The project-root file (and its older name `phantoms.json`) is no longer read; running on without it would silently drop its openings and move sales back into the totals.
- **Fix:** `tjs migrate --dry-run`, then `tjs migrate` (in a year folder it merges every year folder's file). It sizes each entry as the last `taxjson run` opened it, from the books in `work/`; without books it says so: move the file aside, `tjs run`, move it back, `tjs migrate`. Each entry becomes a line dated the day before the account's first row; entries every year sizes the same are one line, entries that open nothing anywhere (no rows, never short) are dropped and listed, and each file is renamed `missing_history.json.migrated` (delete it once satisfied). When the year folders disagree it writes nothing — see the next entry. Then `tjs run` in each year and compare `tjs sum` with your last figures; the dates and quantities are yours to correct.
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/lib/migrate.py` — `MISSING_HISTORY_FILES`, `legacy_message`; `src/taxjson/lib/missing_history.py` — `plan_missing_history_migration`, `project_view`, `apply_missing_history_migration`, `read_legacy_entries`; `src/taxjson/bin/taxjson_run.py` — `_migrate_missing_history`, `_refuse_legacy_project_files`

### "Error: the year folders' missing_history.json files disagree (listed above) — nothing was written for them"
- **Check:** `tjs migrate` lists "The projects disagree on" with each year's quantity (`2024: 10, 2025: not listed`).
- **Cause:** the year folders' `missing_history.json` files, sized as each year's run sized them, open different quantities of a symbol, or list it in some years only; one shared `.tt` line cannot be both.
- **Fix:** decide each: the units you held before the data. Edit the year files to agree and run `tjs migrate` again, or `tjs migrate --write` (each entry as the newest year listing it sizes it), then edit the written line. A year whose totals change was relying on a different opening; `tjs sum` before and after shows it.
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_migrate_missing_history`; `src/taxjson/lib/missing_history.py` — `plan_missing_history_migration`

### "Warning: ATTENTION: inputs/margin/missing_history.tt:3 opens 10 QZQ.US / margin: the position goes short again on 2025-11-03 (40 units) after those units are used up" (or "… on 2024-03-01, but the position is already short on 2024-02-01 (60 units)")
- **Check:** `tjs find-missing-history` shows "the run opens the 10 units its line states" under the position.
- **Cause:** a `.tt` `OPENING ... cost=unknown` line opens its quantity on its date, exactly: the account sells more than that and its purchases in the files, or sells before the line's date.
- **Fix:** raise the quantity to the units held before the data (or add the missing purchase); date the line before the first sale it covers — the day before the account's first row is always early enough. A real short sale needs nothing.
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/lib/missing_history.py` — `short_again_message`, `_apply_fixed_openings`

### "Info: 1 position(s) go short in margin's data (QZQ.TO): booked as short sales closed by a later purchase" although `missing_history.tt` opens QZQ.US (a TOBASE listing of it)
- **Check:** ticker.map or tobase.map has `TOBASE QZQ.US QZQ.TO`, the `OPENING … cost=unknown` line names `QZQ.US`, and `work/<account>_gains.json` → `missing_history_log` says "no rows for this symbol/account" for it; `tjs --version` is 0.27.0.
- **Cause:** the line's symbol was matched as written against books whose rows ticker.map had already renamed: the opening was dropped, the sales became short sales closed by the later purchase (a gain at that purchase's cost), and the raw holdings listed the listing short.
- **Fix:** (issue #23) upgrade: the line's symbol goes through ticker.map and tobase.map as the rows do (the base-currency books open the TOBASE target, the native holdings view the listing the books trade); a mapping added after the line is followed too. Two lines that become one security open its units together when they have one date; on two dates the run stops naming both — write one line.
- **Fixed in:** `v0.27.1`
- **Code:** `src/taxjson/lib/missing_history.py` — `load_missing_history`, `declaration_maps`, `_book_fixed`; `src/taxjson/bin/taxjson_gains.py` — `--native-books`

### "warning: inputs/margin/missing_history.tt:1 opens QZQ.TO / margin, but no row in the data has that symbol and account — nothing was applied" for a holding you simply kept
- **Check:** no row of the account trades the symbol after the line's date (or only its dividends do); `tjs --version` is 0.27.0; the holding is missing from `reports/<account>_holdings.toml`, `tjs sanity` and `tjs t1135`.
- **Cause:** a dated `OPENING … cost=unknown` line was applied only when a later trade of the symbol was in the data.
- **Fix:** (issue #24) upgrade: the line's units are held whether or not a later row trades them; its currency comes from the security's rows, else the books (the base currency; the listing's in the native holdings view). The run now says `note: … its units are held as written` when no row at all has the symbol: check the spelling if it is not a holding you kept. When the currency cannot be told the run stops naming the line.
- **Fixed in:** `v0.27.1`
- **Code:** `src/taxjson/lib/missing_history.py` — `_apply_fixed_openings`, `_opening_currency`, `report_missing_history_log`

### "Error: 1 entry cannot be converted (listed above) — nothing was written or renamed" from `tjs migrate`
- **Check:** `tjs migrate` lists "Cannot convert (no books for the account in a year folder listing it …)"; the account is configured only in an older year folder.
- **Cause:** a line is dated the day before the account's first row in the books (`work/<account>_base.json`) of the year that sized the entry; no year folder listing the entry has books for the account. Before the fix (0.27.0) the date was looked up in the newest year only, the entry was skipped and every `missing_history.json` was renamed `.migrated` anyway, losing it.
- **Fix:** (issue #25) `tjs run` in the year folder whose `taxjson.toml` has the account, then `tjs migrate` again. The files are renamed only once every entry is converted.
- **Fixed in:** `v0.27.1`
- **Code:** `src/taxjson/lib/missing_history.py` — `plan_missing_history_migration`, `apply_missing_history_migration`; `src/taxjson/bin/taxjson_run.py` — `_migrate_missing_history`

### `tjs migrate` lists "2024: 5, 2025: opens nothing (no opening needed) — written: nothing (2025's view …)"
- **Check:** the newest year's last run found the entry's rows never go short (its books are complete), an older year's opened units.
- **Cause:** the newest year listing an entry decides it, an explicit "opens nothing" included; 0.27.0 skipped it and wrote the older year's opening, putting unknown-cost units back into books that no longer need them. A year that does not list the entry, or whose books have no row of it, does not decide.
- **Fix:** (issue #28) nothing to do if the newer books are right: `tjs migrate --write` writes no line for it. If the older opening is right, write the `OPENING … cost=unknown` line yourself.
- **Fixed in:** `v0.27.1`
- **Code:** `src/taxjson/lib/missing_history.py` — `plan_missing_history_migration`, `_view_parts`

### `tjs checklist`: "[!] inputs-committed … ../inputs/ is not in this project's git repository"
- **Check:** `git -C 2025 rev-parse --show-toplevel` and `git -C inputs rev-parse --show-toplevel` name different folders (or the second fails).
- **Cause:** the year folder is its own repository, so the shared `inputs/` beside it is committed nowhere the checklist can see.
- **Fix:** one repository for the whole folder: `git init` in the folder holding the year folders (move a year folder's history in with `git subtree`, or start fresh), then commit `inputs/` and the year folders.
- **Fixed in:** `v0.26.0`
- **Code:** `src/taxjson/lib/checklist.py` — `d_inputs_committed`, `project's git repository`

### `tjs checklist` prints only "Error: [accounts.crypto] is a crypto account but [settings] has no local_timezone" (or "Error: this project still has yf_ticker.map …") instead of the list
- **Check:** `tjs --version` is a development build after v0.24.2; `tjs init` ran on a machine whose zone is UTC or cannot be read (no `local_timezone` written), or the project root holds an old map file such as `yf_ticker.map`.
- **Cause:** the checklist loaded taxjson.toml the way every other command does, and that loader refuses such a config, so a fresh project got the error alone instead of the list with the configure step needing attention.
- **Fix:** upgrade. The checklist now reads the file leniently: `[>] configure` needs attention with what to fix (set `local_timezone`, or run `taxjson migrate`), and the checks that need a loadable config show `[b]` "fix the configuration first (the configure item)" until you fix it; exit 1 (not ready). Only a taxjson.toml with no readable year, country or account names still stops with the error.
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_checklist_config`, `cmd_checklist`; `src/taxjson/lib/checklist.py` — `s_configure`, `CONFIG_FIRST`, `config_error`

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

### "Info: tobase.map: ticker.map decides 2 of its pair(s) otherwise (ticker.map wins)"
- **Check:** the lines under it, `- TOBASE QZAB.US QZA.TO not applied (ticker.map: TOBASE QZAB.US QZZ.TO)`; `tjs update-tobase-map` lists the same pairs.
- **Cause:** a Canadian project reads tobase.map (the interlisted master's pairs) with ticker.map, and ticker.map wins: a `TOBASE`, `JOURNAL`, `GLOBAL`, `DELETE` or dated `RENAME` of either listing, a `TOBASE` that books another listing under the one the pair would move, or a `DISTINCT` pair keeps the master's line from applying.
- **Fix:** nothing, if your line is right (it is what the books use). If the master's pair is right, delete your ticker.map line and re-run. A pair your map pools the same way is never listed.
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/lib/tobase_map.py` — `compute_overlay`; `src/taxjson/bin/taxjson_run.py` — `_say_tobase_map`

### "Warning: QZG.US has 2 row date(s) after 2025-06-30, when its interlisting ended (tobase.map:41: `TOBASE QZG.US QZG.TO`)"
- **Check:** the dates listed; the security name on those rows (`tjs trades`, `tjs events`). The tobase.map line ends `until=2025-06-30`.
- **Cause:** the master records that the pair stopped trading as one security on that date (an acquisition, a delisting). A later row in that ticker may be another company's: US tickers are reused, and the line would pool it with the Canadian listing.
- **Fix:** if those rows are another security, add `DISTINCT QZG.US QZG.TO` to ticker.map (it keeps the two apart at every date; book the old security's earlier rows under a symbol of their own with a dated `.tt` `RENAME` line if the year holds both). If they are the same security (a late corporate-action row), nothing.
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/lib/tobase_map.py` — `until_findings`, `until_message`; `src/taxjson/bin/taxjson_run.py` — `_say_tobase_until`

### "Warning: QZG.US has 3 row date(s) in the books and the ticker is reused: today it names QZ OTHER CO (tobase.map:41: `TOBASE QZG.US QZG.TO`)"
- **Check:** the dates listed and the security name on each row (`tjs trades`, `tjs events`); the tobase.map line carries `reused_by="..."`.
- **Cause:** the master knows the ended US ticker of the pair names another security today, so any row of it — before the end date too, if the books' history is ambiguous — may be that other company's. The line pools every row of it with the Canadian listing.
- **Fix:** if the rows are the old company's, nothing. If some are the other company's, add `DISTINCT QZG.US QZG.TO` to ticker.map and book the old company's rows under a symbol of their own (a dated `.tt` `RENAME` line).
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/lib/tobase_map.py` — `until_findings`, `until_message`; `scripts/build_interlisted.py` — `check_reused`

### `tjs update-tobase-map`: "Retracted: 1 line(s) the master no longer gives or no longer needs" naming `TOBASE QZG.US QZG.TO` (or "ATTENTION: … line(s) you edited are lines the master RETRACTED")
- **Check:** the reason printed beside the line; the master's entry in `src/taxjson/data/interlisted.toml` (its `retracted` list, found by the FIGI in the line's marker).
- **Cause:** an earlier master shipped a wrong pair (for instance an ended pair's US ticker that was another company's). A correction retracts it for good; an unedited line is removed, an edited one kept and flagged.
- **Fix:** `tjs update-tobase-map --write`, then `tjs run`. For a flagged edited line, check it and delete it if it was the master's mistake. Rows of the retracted ticker that were pooled before are no longer: check the year's gains.
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/lib/tobase_map.py` — `plan_update`, `retracted_listings`, `is_edited`; `scripts/build_interlisted.py` — `merge_previous`, `load_history`

### `tjs ticker-map --suggest` lists a pair to verify such as `TOBASE QZOR.US QZOR.TO` for a US share you hold
- **Check:** `grep -n 'QZOR.TO' tobase.map`: a tobase.map line pools an OTC listing under a TSX Venture issuer with the same letters.
- **Cause:** the listing-pair check read tobase.map's pairs (and their targets) as sightings of the TSX listing in the books, so a US company's shares looked like one side of an interlisting.
- **Fix:** upgrade; the pair is no longer suggested. Never add such a line: it would pool two companies.
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/lib/map_hygiene.py` — `map_gaps`, `own_renames`; `src/taxjson/lib/tobase_map.py` — `own_map_text`

### `tjs t1135` lists a TSX-held `BEP.UN` (booked as `BEP.US`) under the USA, or warns "`T1135 BEP.UN.TO` matches no symbol in the books"
- **Check:** the tobase.map line `TOBASE BEP.UN.TO BEP.US … country=BMU`; `tjs t1135 --json` (`properties`, `country`).
- **Cause:** tobase.map books a foreign-domiciled issuer's pair under its US listing (its dividends and T1135 status are foreign); older versions classified that symbol by its `.US` suffix (USA) and did not follow a `T1135` line naming the TSX listing through the `TOBASE` line.
- **Fix:** upgrade, `tjs update-tobase-map --write` (the lines gain `country=`), `tjs run`, `tjs t1135`. A `T1135 SYMBOL COUNTRY` line still overrides.
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/lib/tobase_map.py` — `t1135_countries`, `issuer_country`; `src/taxjson/bin/taxjson_t1135.py` — `build_report`

### `tjs update-tobase-map --write` added back a master line I deleted, or duplicated a line I edited
- **Check:** the `## --- Master lines you removed` section of tobase.map; `tjs update-tobase-map` lists "Left as written" (your edits) and "Removed by you".
- **Cause:** older versions could not tell a deleted line from a new one, nor an edited line (another target, the direction reversed) from a retracted one.
- **Fix:** upgrade. A deleted master line is recorded as `# removed: master:<FIGI> TOBASE A B` and stays out (delete that line and copy the master's line back to have it again); a commented-out marked line is an opt-out too; an edited line is yours and the master's line for the same listing is not added beside it. Your comment lines are kept.
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/lib/tobase_map.py` — `plan_update`, `parse_tobase`, `line_hash`, `is_edited`

### `tjs update-tobase-map` lists no pair for a TSX class share or trust unit (`QZK.B.TO`, `QZR.UN.TO`) that trades over the counter in the US
- **Check:** `grep -n 'QZR.UN.TO' tobase.map`; the master's entry in `src/taxjson/data/interlisted.toml` (`ca = ["QZR.UN.TO"]`).
- **Cause:** the TMX issuer list the master is built from names an issuer by its bare root (`QZR`), and OpenFIGI knows the line only under its class or unit spelling, so masters before the first refresh left such issuers out (their US OTC line, and some exchange pairs, were missing).
- **Fix:** upgrade taxjson, then `tjs update-tobase-map --write` and `tjs run`. Before that, a `TOBASE QZRUF.US QZR.UN.TO` line in ticker.map pools the two.
- **Fixed in:** `v0.27.0`
- **Code:** `scripts/build_interlisted.py` — `CLASS_SPELLINGS`, `build`

### "Error: 1 ticker.map problem(s)" with "tobase.map:12: tobase.map holds `TOBASE FROM TO` and `DISTINCT A B` lines only"
- **Check:** the named tobase.map line; `tjs update-tobase-map` lists marked lines you edited.
- **Cause:** tobase.map is read with ticker.map, and a line it cannot use would drop a pair silently, so the run stops as for a ticker.map problem.
- **Fix:** move your own rule to ticker.map (any keyword lives there) and delete it from tobase.map, or run `tjs update-tobase-map --write` to lay the file out again (your own unmarked `TOBASE` / `DISTINCT` lines are kept).
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/lib/tobase_map.py` — `parse_tobase`; `src/taxjson/bin/taxjson_ticker_map.py` — `_parse_map_file`

### `tjs checklist`: "[!] tobase-map … tobase.map is from the master of 2026-01-01; this taxjson has the one of 2026-10-01"
- **Check:** `tjs update-tobase-map` (a dry run) lists what an update adds, ends and retracts, and the pairs that name a symbol of your books.
- **Cause:** an upgrade installed a newer interlisted master; the project's tobase.map was made from an older one (its `# master-generated` line).
- **Fix:** `tjs update-tobase-map --write` (the previous file is kept as `tobase.map.bak`), then `tjs run`. In a project with several year folders the years read one shared tobase.map (since v0.27.1): run it once, in any year folder or in the folder holding them, then `tjs run` in each year it names (a filed year's `tjs check-filed` shows whether it moved the filed figures). Years that still keep a copy each: `tjs migrate` there first.
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/lib/checklist.py` — `s_tobase_map`; `src/taxjson/bin/taxjson_run.py` — `cmd_update_tobase_map`

### `tjs update-tobase-map`: "Retracted: … `DISTINCT QZD.US QZD.TO`: not needed: look-alike listings are never joined" (or `tjs checklist`: "[!] tobase-map … holds 1 DISTINCT line(s) an earlier version wrote")
- **Check:** `grep -n '^DISTINCT' tobase.map` (in a multi-year project, the `tobase.map` beside the year folders): lines marked `# master:<FIGI>` under "Depositary receipts the books hold".
- **Cause:** v0.27.0 wrote a `DISTINCT` line for each Canadian depositary receipt (CDR) the books held whose root is a US ticker. taxjson never joins two listings because their letters match (only a broker's journal evidence or a `TOBASE` line joins), and the interlisted master itself keeps a CDR apart from its US share (no journal join, no `ticker-map --suggest` pair, no MAP-GAP, no loss-radar warning), so the lines say nothing. Nothing in the books changes when they go.
- **Fix:** `tjs update-tobase-map --write` removes the unedited ones (the previous file kept as `tobase.map.bak`). A line you edited is kept and listed under ATTENTION: delete it, or keep it (harmless). A `DISTINCT` line of your own in ticker.map stays as written.
- **Fixed in:** `v0.27.1`
- **Code:** `src/taxjson/lib/tobase_map.py` — `plan_update`, `DISTINCT_NOT_NEEDED`, `receipt_pairs`; `src/taxjson/lib/cross_listings.py` — `shown_apart`, `RECEIPT`; `src/taxjson/lib/checklist.py` — `s_tobase_map`

### `tjs tips --online`: "CDR-PAIR … is a CDR over QZD.US … Add `DISTINCT QZD.US QZD.TO` to ticker.map to record this and silence the pair"
- **Check:** `tjs --version`; the pair is a Canadian depositary receipt and the US share it is over.
- **Cause:** the advice predates v0.27.1, which stopped joining listings because their letters match: a `DISTINCT` line for a receipt says nothing. `taxjson init` and `new-year` help and output also still said tobase.map is copied into each year folder (it is one file beside them), `run -h` said the holdings cross-check needs `holdings = [...]` (holdings/ is found without a setting), and the account folders' README put the slips in `inputs/slips/` (a year folder's own `YYYY/inputs/slips/`).
- **Fix:** upgrade; nothing to do for the pair: look-alike listings are never joined. A `DISTINCT` line already written is harmless.
- **Fixed in:** unreleased
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_tips`, `look-alike listings are never`; `src/taxjson/lib/config_template.py` — `input_readme`

### "Error: two tobase.map files for this year: [settings] tobase_map = '../tobase.map' names ../tobase.map (shared by every year), and this folder holds a tobase.map of its own"
- **Check:** `ls 2025/tobase.map ../tobase.map` and `grep -n tobase_map 2025/taxjson.toml` (each year folder); `tjs years` (run in the folder holding the years) names the shared file and any year that keeps a copy.
- **Cause:** since v0.27.1 the years of a multi-year project read one `tobase.map` beside the year folders (`[settings] tobase_map`). A year folder that has the setting and still holds a copy of its own would leave one of the two silently unread, so every command refuses it (a copy restored from git, or a setting copied with `tjs align` into a year that kept its copy).
- **Fix:** `tjs migrate` in the folder holding the years (or `tjs -C .. migrate` from a year folder) makes the copies one shared file: identical copies at once, each kept as `tobase.map.bak`; copies that differ are listed and need `--write` (the newest year's file is kept). Or delete the year's own `tobase.map`, or remove its `tobase_map` setting (it then reads its own copy, the layout before v0.27.1).
- **Fixed in:** `v0.27.1`
- **Code:** `src/taxjson/lib/project_layout.py` — `tobase_both_problem`, `tobase_setting`, `setting_problems`; `src/taxjson/bin/taxjson_run.py` — `_refuse_folder_settings`, `_migrate_shared_tobase`

### `tjs migrate`: "the year folders' tobase.map copies differ (listed above) — nothing was written for them"
- **Check:** the lines `tjs migrate` lists: "Lines of yours only 2024/tobase.map has" (your own lines and edited marked lines that the newest year's copy lacks) and how many master lines of another master version each copy has. `tjs migrate --dry-run` shows the plan without writing.
- **Cause:** the year folders of a project made before v0.27.1 keep a copy of tobase.map each, and they differ (an update run in one year only, or a line you added in one year). One file every year reads needs one text, and taxjson does not guess which of your lines to keep.
- **Fix:** `tjs migrate --write` keeps the newest year's file as the shared `tobase.map` (each copy kept as `tobase.map.bak`); then add any listed line of yours you still need to the shared file (or to that year's ticker.map, which wins over it), and run `tjs update-tobase-map --write` once to bring the shared file to the installed master. `tjs run` in each year.
- **Fixed in:** `v0.27.1`
- **Code:** `src/taxjson/lib/tobase_map.py` — `plan_shared`, `apply_shared`; `src/taxjson/bin/taxjson_run.py` — `_migrate_shared_tobase`, `cmd_migrate`

### "Error: `taxjson update-tobase-map` is Canada-only (Canada only for now: the interlisted pairs pool identical property …); this project is country = "usa""
- **Check:** `[settings] country` in taxjson.toml.
- **Cause:** the interlisted master's pairs apply to Canadian projects only for now (tax-logic US-XLIST-05); a US project does not read a tobase.map either (an Info line says so when one is there).
- **Fix:** in a US project, write the pairs you need in ticker.map (`TOBASE` / `DISTINCT`).
- **Fixed in:** —
- **Code:** `src/taxjson/lib/country.py` — `COMMAND_COUNTRY`, `COMMAND_WHY`; `src/taxjson/bin/taxjson_run.py` — `_say_tobase_map`

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

### `tjs format-map` keeps an old header ("# ticker.map — symbol rules for the taxjson pipeline. … JOURNAL from to A Norbert's Gambit pair …") above the new one, or "Info: a comment block looks like an old taxjson header you edited — kept; delete it if it no longer applies"
- **Check:** ticker.map holds two headers after `tjs format-map --write`: the `## ticker.map: standing truths …` one and, below it, a `#` block describing GLOBAL, TOBASE, JOURNAL and DELETE. The Info line names the first line of a block kept because it differs from every header taxjson wrote; "Info: 1 comment block(s) still describe JOURNAL or a dated RENAME as a ticker.map rule" names a comment of yours that documents the legacy dated events.
- **Cause:** format-map replaced only the headers of the recent `taxjson init` templates, and only a paragraph standing alone: the earliest projects' header (written before `taxjson init` had a template), a header re-wrapped or re-cased, and one with your notes directly below its last line were kept as your notes.
- **Fix:** upgrade, then `tjs format-map` (the dry run shows the old header removed and says how many lines) and `tjs format-map --write`; `--check` fails while an old header is there. A block it keeps as edited is yours to delete. Your comments that mention JOURNAL or a dated RENAME are kept: a journal or a ticker change on a date is a `.tt` line now (see "ticker.map holds 1 JOURNAL line(s)" below).
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/ticker_map_legacy.py` — `is_legacy_paragraph`, `near_legacy`, `corpus_tables`; `src/taxjson/lib/ticker_map_format.py` — `_scan_template`, `_legacy_spans`, `_dated_comment_blocks`; `src/taxjson/bin/taxjson_run.py` — `_format_map_header_notes`

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

### A file outside the project was overwritten by `tjs run`: a symlink in `work/` (such as `work/loss_overrides.json`) pointed at it
- **Check:** `ls -l work/` shows a symlink (`->`) among the generated files, and the file it points at now holds taxjson's state (for `loss_overrides.json`: `{"schema_version": 1, "overrides": []}`).
- **Cause:** the run wrote `work/loss_overrides.json` (on every run, even with no ALLOWLOSS line) and a few other generated files (the `.diag` diagnostics, the skipped-accounts and own-account-move state, the missing-history marker, the empty `to_base.csv`, the code stamp, a new elections manifest, a pending-elections file) with a plain write, which follows a symlink at the name and overwrites its target.
- **Fix:** upgrade: every generated file is written to a temp file of its own and renamed over the name, so a link there is replaced by the new file and its target is never opened. On an older release, remove the symlinks from `work/` (it holds only generated files) and restore the overwritten file from a backup.
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/lib/loss_overrides.py` — `write_state`; `src/taxjson/lib/safe_write.py` — `write_atomic`; `src/taxjson/bin/taxjson_run.py` — `_record_skipped_accounts`, `stage_own_account_moves`

### "Error: folder(s) that are symlinks to outside the project — taxjson writes there; nothing was run: work/ -> /mnt/scratch/work"
- **Check:** `ls -ld work reports filed export inputs inputs/*` in the project shows the named folder as a link (`->`) to a path outside the project folder. Every command stops with exit 2.
- **Cause:** taxjson writes your books (`work/`), reports, the filed lock and, in an account's `inputs/` folder, the elections and crypto-send decisions. A folder that is a link leaving the project sent those files wherever it points, so such a link is refused, as a `ticker.map` link outside the project is. A link to a folder inside the project is fine.
- **Fix:** replace the link with a real folder: `rm work && mkdir work` (copy the contents in first if you need them; `work/` is rebuilt by `tjs run`), or run taxjson in the folder the link points into.
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_refuse_outside_dir_links`, `_WRITTEN_DIRS`, `load_config`; `src/taxjson/lib/safe_write.py` — `link_outside`

### "Warning: the project folder, inputs/ can be read by other users of this computer (made by an older taxjson or another program; new files are owner-only)"
- **Check:** `ls -ld . inputs reports` in the project: a mode other than `drwx------` (for example `drwxrwxr-x`) on any of them. The warning shows once per `tjs run`.
- **Cause:** taxjson creates every folder and file owner-only (0700 / 0600), but folders made by an older release, by `mkdir`, `git clone` or a copy keep your shell's permissions, and other accounts on the machine can then list or read your statements and books.
- **Fix:** run the command the warning names once: `chmod -R go-rwx <project>`. Nothing else changes; the warning stops.
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_loose_project_dirs`, `can be read by other users`

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

### "Warning: inputs/margin/extra.tt:2: QZA has no market suffix: a pool of its own, apart from QZA.US in the books"
- **Check:** the `.tt` line names the share without its market suffix (`QZA`), while the broker's rows book it as `QZA.US` or `QZA.TO`; `tjs list` shows both, the broker's sale of the suffixed listing going short.
- **Cause:** a share listing is spelled with its suffix in the books; a bare symbol is its own ACB pool (a coin's spelling). Before the fix the warning reached only the account's `.sum`, and with exports shared by every year (`inputs_dir`) not even that.
- **Fix:** write the symbol as the warning names it (`QZA.US`) on that `.tt` line and `tjs run`.
- **Fixed in:** unreleased
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_echo_tt_bare_symbols`; `src/taxjson/bin/taxjson_convert_tt.py` — `_warn_bare_equity_symbol`, `--equity`

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

### "Warning: the same broker account (#ab12cd) feeds two taxjson accounts, qt and wb (0 identical row(s))" for exports of two DIFFERENT brokers
- **Check:** the two folders hold different brokers' exports (`tjs run` names the broker of each file) that print the same account number.
- **Cause:** the check compared the account number alone; a Questrade, an RBC and a Webull account can carry the same 8-digit number.
- **Fix:** upgrade: the check keys on the broker and the account number. Nothing to change in the inputs.
- **Fixed in:** unreleased
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_warn_shared_broker_accounts`, `_source_brokerages`

### "Warning: Duplicates: a.csv and b.csv both hold 1 identical row(s)" … "Booked ONCE (read as the same row exported twice)", or "Warning: Duplicates: margin_extra.tt line (…) repeats the exported row in ib_2025.csv" … "so BOTH are booked"
- **Check:** the warning names both files and the row; `tjs trades` for that day shows what was booked.
- **Cause:** exports carry no row id. An identical row in two overlapping exports of one account is read as the same trade exported twice, and the run says so when the files' overlap cannot prove it (they share only that row). A hand-kept `.tt` line that repeats an exported trade has no matching id, so both are booked.
- **Fix:** if the two export rows were really two trades, enter the second as a `.tt` line. If the `.tt` line is the exported trade, delete it. Overlapping downloads are otherwise fine to keep.
- **Fixed in:** `v0.17.0`
- **Code:** `src/taxjson/bin/taxjson_sort.py` — `plan_dedup`, `_tt_near_duplicates`, `Booked ONCE (read as the same row exported twice)`

## Missing purchase history and transfers

### "Warning: 2 positions sold in 2025 with no purchase in your files, not in missing_history.tt" (or "Info: 1 position(s) go short in margin's data (QZQ.TO)")
- **Check:** `tjs find-missing-history` lists each pair under "AFFECTS 2025" with its first negative date and the sales it touches.
- **Cause:** the exports start after the shares were bought (or the shares were transferred in), so the sale has nothing to close. It is booked as a short and its gain is in no total.
- **Fix:** in this order: add an older export that holds the purchase to `inputs/<account>/`; or enter the purchase as a `.tt` BUYSELL line with its real date and cost (`tjs find-missing-history --write-purchases` drafts these from IB's Basis or a transfer's stated book value); only when the history cannot be recovered, `tjs find-missing-history --write-missing-history` writes `OPENING <date> <SYMBOL> <qty> cost=unknown` lines into `inputs/<account>/missing_history.tt`, and those sales must then be reported by hand. docs/getting-started.md step 5 walks through it.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/first_run.py` — `render`, `with no purchase in your files, not in `; `src/taxjson/bin/taxjson_run.py` — `_short_positions_note`; `src/taxjson/lib/missing_history.py` — `detect_missing_history`

### "Info: 1 position(s) go short in margin's data (QZD.TO)" or "Warning: 1 position sold in 2025 with no purchase in your files, not in missing_history.tt: QZD.TO (margin)" after a Norbert's gambit, with a ticker.map `TOBASE` line (or none) instead of `JOURNAL`
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

### "Error: account margin: the opening balance of QZS.US dated 2025-01-31 would leave out 1 short cover(s) of the 2025 tax year (first: 2025-01-06 cover of 10 QZS.US: a buy of 10 that closes a short position)"; on an older release the run went on and the cover's gain was in no total
- **Check:** the account has an `OPENING` line (`inputs/<account>/opening_<date>.tt`) dated in the tax year, and its rows of that symbol before the snapshot include a buy that closes a short position (a short sale, or a written option bought back). `tjs sum` on an older release has no disposition for that cover.
- **Cause:** an opening snapshot leaves the account's earlier rows of its symbols out of the books (tax-logic CA-OPEN-03 / US-OPEN-03), and a left-out sale of the tax year stops the run. Only sales (negative quantities) were checked: a cover is a positive-quantity buy, so a cover of the tax year was dropped silently and the short sale's gain or loss fell out of the year in both countries. The check now reads each left-out row's position, walked back from the snapshot's quantity, and stops on a cover too (including a buy that crosses from short to long). The walk undoes each split of the security once, whichever account's rows carry it, and orders a row with no time at 00:00:00, as the engines do.
- **Fix:** upgrade and `tjs run`. Take the snapshot from a statement before the year's first sale or cover of that symbol (December 31 of the year before), or remove the `OPENING` line and supply the history.
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/lib/opening.py` — `apply_opening_cutoff`, `_realizations_left_out`, `_when`

## Holdings

### "Info: 5 accounts with open positions and no holdings file to check them against" (or `tjs sanity`: "Error: no arguments, no snapshot in the project's holdings/ folder, and no account in taxjson.toml declares `holdings = [...]`")
- **Check:** `tjs checklist` shows step `sanity` as `[m]`; `ls holdings/` is empty.
- **Cause:** nothing compares the books' positions with the broker's own positions report yet.
- **Fix:** save the broker's positions snapshot (a `[[holding]]` TOML, as a download tool writes it) in the year's `holdings/` folder, named for the account (`margin_holdings.toml`) or carrying its broker account id in `[meta] account`; or add `holdings = ["~/holdings/margin.toml"]` under `[accounts.margin]`. Then `tjs sanity` and `tjs run` check it every time. One-off: `tjs sanity margin=/full/path/positions.toml`.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/first_run.py` — `unchecked_accounts`; `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `no snapshot in the project's holdings/`

### `tjs sanity`: "Info: holdings/U1***_positions.toml: no account claims it — add its broker account id to the account"
- **Check:** the file's `[meta] account` (masked here) is in no account's `account` or `broker_accounts`, its name does not start with an account name, and no account's `holdings = [...]` lists it.
- **Cause:** a snapshot in `holdings/` is matched to its account by the broker account id it states, else by its file name; this one matches neither, so it is not compared. (Before the fix, a file an account lists in its own `holdings = [...]` was named here too, though that account compares it.)
- **Fix:** add the id under its account (`broker_accounts = ["…"]`), rename the file `<account>_holdings.toml`, or list it in the account's `holdings = [...]`. An id two accounts declare is refused the same way.
- **Fixed in:** `v0.26.0`
- **Code:** `src/taxjson/lib/holdings_dir.py` — `discover`, `listed_files`, `claims it`

### `tjs sanity`: "INCOME ON SHARES THE BOOKS DO NOT HOLD" for a dividend paid after you sold, or "Info: cost not compared for registered accounts (tfsa): book cost there is not tax cost"
- **Check:** the dividend's record date (its `REC mm/dd/yy`, or the payer's notice) against your sale's settlement date; for the cost line, the account's `type = "sheltered"` in taxjson.toml.
- **Cause:** the check compared the share count a dividend states with the books' position on the pay date (or required an exact match), so a sale after the record date — the dividend paid weeks later with the position at 0 — read as income on shares not held. A registered account's broker book cost was compared with the books' cost, but there it is not a tax cost (an in-kind transfer in resets it to the market value).
- **Fix:** upgrade: entitlement is the record date (the row's, a `.tt` `record=`, or the description's `REC`), else any time in the 45 days to the pay date — the position of the security as the books spelled it then (a ticker change between the record date and the pay date — a broker's, a dated `.tt` RENAME, a ticker.map rename — is followed back to the old symbol), settled on the record date or traded by the eve of the ex-date on the market's own calendar (T+2 before May 2024, T+1 since — a broker's settlement date can follow another market's holiday); only a payment on more shares than the books held then (or on a symbol never held) is listed. Costs are compared for taxable accounts only; a registered account's quantities are still compared.
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/lib/positions_check.py` — `income_share_mismatches`, `INCOME_WINDOW_DAYS`, `_record_date_of`, `_max_held`, `held_as`, `last_entitled_trade_day`; `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `cost not compared for registered accounts`

### `tjs sanity`: "VQQ.US MISSING_IN_HOLDINGS -500 0" for a position whose shares bought before the data were sold (covered by missing history)
- **Check:** `tjs find-missing-history` lists the symbol as covered (a `.tt` `OPENING ... cost=unknown` line); the holdings snapshot is dated before the books' last row (sanity compares the books' positions on its date); the dividend check may also list a dividend "on a share count the books did not hold".
- **Cause:** sanity rebuilt the books' positions on the snapshot's date from the merged rows only (work/<account>_base.json), without the missing-history openings the gains run adds, so the sale of those units read as a short.
- **Fix:** upgrade: every positions view uses the books' own positions, the missing-history units included (`taxjson sanity`, the end-of-run holdings check, the dividend share-count check). A sale with no purchase that missing history does not cover is still flagged.
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/lib/positions_check.py` — `book_rows`; `src/taxjson/lib/missing_history.py` — `openings_from_log`; `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`

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
- **Fix:** `tjs elect margin --set EVENT_ID=ELECTION` (add `--hint KEY=VALUE` where required), or run `tjs run` at a terminal to be asked; then run again. A spin-off or merger of a sheltered account is not asked by default (next entry); with `sheltered_elections = "ask"` it is — the election sets the cost its holdings carry, and no tax in the account depends on it: the prompt and `tjs elect --pending` say "sheltered account: this election sets the holdings' cost in the books — no tax in this account; its holdings still count for the superficial-loss rule" (a US project: the wash-sale rule).
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_corp_actions.py` — `corp-action event(s) need an election `, `sheltered_note`; `src/taxjson/bin/taxjson_run.py` — `cmd_elect`, `_print_pending`

### "Info: sheltered account lira: spin-off SPNCO.TO on 2025-03-03 (event …) booked at $0 cost for the distributed shares"
- **Check:** `tjs spinoffs` shows the event with election `sheltered_default` and the new shares' cost of 0; `tjs elect --pending` does not list it.
- **Cause:** a spin-off in a sheltered account (RRSP, LIRA, TFSA, RESP, RRIF; US: IRA, Roth, 401(k), HSA, 529) is booked without asking: nothing is taxed inside the account, and nothing taxable reads its cost (an in-kind move uses fair market value; the superficial-loss / wash-sale rule counts units). The new shares start at $0 and the parent keeps its whole cost; a merger's new shares take the old shares' cost ("booked with the old shares' cost carried to the new shares"). It used to be asked like a taxable account's event, stopping `run --no-input`, `run --strict` and the checklist until answered.
- **Fix:** nothing to do. For a real cost in the holdings view, `tjs elect lira --set EVENT_ID=ELECTION` (the saved election wins); `sheltered_elections = "ask"` in `[settings]` asks for every such event again.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_note_sheltered_defaults`, `_sheltered_elections`; `src/taxjson/lib/corp_actions.py` — `sheltered_default_rows`, `sheltered_default_text`; `src/taxjson/bin/taxjson_corp_actions.py` — `--sheltered-elections`

### "Warning: margin: spin-off SPNC.US on 2025-06-03 (event …) is booked at $0"
- **Check:** `tjs spinoffs` shows the election, the value used and the cost booked.
- **Cause:** the election has no `fmv_per_share` saved (a hand-edited or older manifest) and the broker reported no value: no dividend income is booked and the new shares cost $0, so a later sale overstates the gain. The same warning exists for a merger booked at $0 (there `fmv_per_share=0` still means "not known yet") and a spin-off with $0 allocated cost. A spin-off whose `fmv_per_share=0` you wrote yourself is a declared $0 cost: an Info (next entry).
- **Fix:** set the value: `tjs elect margin --set EVENT_ID=ELECTION --hint fmv_per_share=<value>` (the line printed with the warning), then `tjs run`.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_warn_zero_value_spinoffs`

### "Warning: 1 position at a $0 cost (1 still held): SPNW.TO (margin). Run `taxjson find-missing-history`" for a spin-off you elected with `fmv_per_share=0`
- **Check:** `tjs elect margin` shows the event with `hints: fmv_per_share=0`; `tjs find-missing-history` listed the shares under "$0-COST SHARES STILL HELD", and the checklist flagged the elections step ("spin-off/merger(s) booked at $0").
- **Cause:** a spin-off whose shares truly came at no value (a warrant distributed for nothing) is answered by saving its election with `--hint fmv_per_share=0`, but every report read that $0 like a value the broker left out. Now a spin-off election whose `fmv_per_share` you wrote as 0 is a declared $0 cost: the run says "Info: margin: spin-off SPNW.TO on 2025-06-03 (event …) is booked at the $0 value you declared (fmv_per_share=0) …" and, in the closing summary, "Info: 1 position at the $0 cost you declared …"; find-missing-history lists it under "DECLARED $0 COST — answered by your election"; the checklist and `tjs spinoffs` do not flag it. A $0 cost with no value saved (the broker gave none and no hint is in the manifest) is still a Warning.
- **Fix:** nothing to do. To give the shares a value later: `tjs elect margin --redo --event EVENT_ID` (or `--set EVENT_ID=ELECTION --hint fmv_per_share=<value>`), then `tjs run`.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/corp_actions.py` — `declares_zero_value`, `declared_zero_value_events`, `declared_zero_value_text`; `src/taxjson/lib/first_run.py` — `declared_zero_cost`, `zero_cost_positions`; `src/taxjson/lib/missing_history.py` — `detect_zero_basis_acquisitions`; `src/taxjson/bin/taxjson_missing_history.py` — `DECLARED $0 COST`; `src/taxjson/bin/taxjson_run.py` — `_warn_zero_value_spinoffs`

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
- **Fixed in:** `v0.24.1`
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
- **Fixed in:** `v0.24.1`
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

### "Warning: QZK.US: the broker cancelled (Ca) a trade of 6 @ 10 on 2025-02-03, but the original fill is in none of this account's inputs" after "Info: the broker cancelled (Ca) 4 of the QZK.US order of 10 @ 10 … The order is booked as 6"
- **Check:** IB cancelled one order in two parts (two `Ca` rows, in one statement or in two), and together they cancel the whole order: the second cancels exactly what the first left. `tjs sum` books a sale of the second quantity that never happened.
- **Cause:** the cancellation pairing (`taxjson-merge2` across statements, and the IB parser within one) reduced the order by the first cancellation, then looked for an exact match of the second on the order's ORIGINAL size and for a partial match on the reduced one. Cancelling exactly the rest matched neither, so it stayed booked as a reversing trade: a phantom sale with a wrong gain, and the wrong cost left on the shares.
- **Fix:** upgrade and `tjs run`. Both searches now read the order as the earlier cancellations left it; the last cancellation removes the order ("dropped the rest (6) of the QZK.US trade of 10 …").
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/lib/trade_cancel.py` — `pair_cancellations`, `trade_cancel_what`; `src/taxjson/bin/taxjson_merge2.py` — `cancel_trade_pairs`

### "Warning: QZK.US: the broker cancelled (Ca) a trade of 440 @ 10 on 2025-02-03, but the original fill is in none of this account's inputs" after "Info: the broker cancelled (Ca) 40 of the QZK.US order of 440 @ 10 … The order is booked as 400"
- **Check:** IB lists two `Ca` rows for one order: one cancelling one execution (40), then one cancelling the whole order (440, the order's full size). `tjs sum` books a sale of 440 that never happened.
- **Cause:** the first cancellation reduced the order to 400; the second, the whole order's size, then matched neither the 400 left (exactly) nor a part of it, so it stayed booked as a reversing trade: a phantom sale of 440 in both countries' gains.
- **Fix:** upgrade and `tjs run`. When nothing matches the order as it stands but exactly one order an earlier cancellation reduced matches the cancellation at its original size, the order is removed in full, with a note ("the broker then cancelled (Ca) the whole QZK.US order of 440 … the order is removed in full"). Two such orders of the same size are not told apart by a guess: the warning stays.
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/lib/trade_cancel.py` — `pair_cancellations`, `overlaps`; `src/taxjson/bin/taxjson_merge2.py` — `cancel_trade_pairs`; `src/taxjson/lib/brokerages/ib_extractor.py` — `the whole`

### IB: "Warning: U1234567.csv: the statement has no Cash Report" or "Error: U1***.csv: parsed rows do not reconcile with IB's own Cash Report"
- **Check:** the file has no `Cash Report,Header,…` lines (a Flex query or a customised statement). For the error, the next line names the currency and the line, e.g. `USD Dividends: parsed 2.50 vs Cash Report 3.50 (diff -1.00)`.
- **Cause:** taxjson checks the money it parsed against IB's own Cash Report totals, per currency: dividends, payments in lieu, withholding, interest, other fees, commissions and trades. Without the section the check is off, and the run says so. A mismatch means a row was dropped, doubled or mis-signed (often an edited statement), and the parse stops.
- **Fix:** export the Activity Statement with the Cash Report section (Flex: add it to the query). On the error, download the statement again and put it in `inputs/<account>/` unedited.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `no Cash Report — parsed money is NOT reconciled`, `parsed rows do not reconcile with IB's own`

### "Warning: Webull exports for margin end 2025-10-06 with open positions (QZT270115C00090000.US 14); download the rest of 2026"
- **Check:** the details say how the end was read: an IB statement's Period, an RBC export's "as of" date, a Webull export's date range or trading-summary year, or — for a broker whose export names no end (Questrade, a generic mapping) — the last row. `tjs checklist` shows `[!]` (or `[>]`, the next step) on export-coverage, counting the positions instead of naming them: "Webull exports for margin end 2025-10-06 with 5 open position(s) — `taxjson list margin 2025-10-06` lists them; next recorded activity: 2026-06-18 (wb_2026_manual.tt)" (the run's Warning names the same command and date): `tjs list <account> <end date>` shows what was open at the end, and the next activity a later `.tt` line or export of the account records on those positions (any date after the end) shows when they moved next.
- **Cause:** that broker's exports for the account stop before the tax year's end (before today in the year still running; an explicit end may trail today by 14 days) while it still holds positions there, so its later sales, option expiries and assignments, and income are not in the books — and nothing else says so. Only positions still open after the account's later rows of any source, dated inside the gap (after the end, up to Dec 31 of a finished year or today), are named: a `.tt` line that closes one (a hand-entered sale or expiry), another broker's sale or a transfer-out counts, and a broker whose positions are all closed is never listed — when `.tt` lines closed them, the run says "Info: … the positions open at the export end (…) were closed by .tt lines — no export needed" instead. An end read from the last row is flagged only after more than 30 days of the year with no row.
- **Fix:** download the broker's activity for the rest of the year into `inputs/<account>/` and `tjs run`. If the broker really had no activity in the account after that end (an account you stopped using), answer "No <broker> activity in <account> after <end>?" with `tjs checklist --done export-coverage` — whatever the end was read from; the run then says a note. The mark answers the gaps shown when you made it (account, broker and end; `checklist.json` keeps them under `answers`): a new gap, or a later end after you add an export that is still short, asks again. An option the warning lists as "expired after it with no expiry row" expired inside the gap: the missing export holds its expiry, assignment or buy-back.
- **Fixed in:** `v0.24.1`
- **Code:** `src/taxjson/lib/export_coverage.py` — `find_gaps`, `account_gaps`, `open_positions`, `held_at_broker`, `closed_later`, `info_message`, `message`, `question_text`, `Gap`, `detail`, `next_activity`, `list_command`; `src/taxjson/bin/taxjson_run.py` — `_say_export_coverage`, `_mark_answers`; `src/taxjson/lib/checklist.py` — `d_export_coverage`, `question_answers`, `apply_override`

### Export coverage (before release): no warning for an export saved under its account-number name, or a false one ("Questrade exports for lira end 2025-10-27 …" when the export runs to December; "… with open positions (QZD.U.TO 300)" after a Norbert's gambit; "… end 2024-12-31" beside a current Webull file; "RBC Direct Investing exports for margin end … with open positions (QZB270115C00050000.TO -8 …)" for calls bought in an opening `.tt` and sold at RBC)
- **Check:** `tjs checklist` shows `[!] export-coverage`; the named positions are not open at that broker in `tjs list <account> <end date>`, or the export's file name carries the account number (`U5550001_2026.csv`, `55500001.csv`). <!-- pii-ok -->
- **Cause:** the check matched the books' rows to their export by the real file name, but each row carries the name as the parse showed it, with the account number masked — so an id-named export's rows were never counted (a short export went unnoticed) and, when only its transfer or corporate-action rows matched, their date was taken as the end. A transfer sidecar was read without the books' renames (a journal's legs, a `TOBASE` line), a trading summary's year was taken as the end although a later file had rows, and a broker's own sale of a position another source opened (an opening `.tt` line) read as a written option still open there.
- **Fix:** upgrade: rows are matched by the masked name and its key, sidecar and corporate-action rows go through the books' renames, a row past a summary's year dates the end, and a broker's position counts only as far as the account's books hold it too, on the same side.
- **Fixed in:** `v0.24.1`
- **Code:** `src/taxjson/lib/export_coverage.py` — `file_source_id`, `row_source_ids`, `add_source`, `source_broker`, `_book_renames`, `held_at_broker`, `account_gaps`; `src/taxjson/lib/xlist_loss_radar.py` — `_source_brokers`

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
- **Fix:** re-run `tjs run` on a release with the fix: a code's designators (warrant, right, preferred, the class letter; never UNITS, which trusts and funds put in their distribution wording; a spinoff leg's by its own name, never its parent's class letter) are read over all of its descriptions and a listing whose name states none is never the code's (and the other way round); the account's own later trade under the real ticker, described like the code's warrant rows, resolves it (`D0000001 → QZDW.US (the account's own rows of QZDW.US are described …)`), and the spinoff chain books under that ticker. On an older install add `GLOBAL D0000001.US QZDW.US` to `ticker.map`.
- **Fixed in:** `v0.24.1`
- **Code:** `src/taxjson/lib/symbol_codes.py` — `code_designators`, `designators_agree`, `resolve`, `the account's own rows of`; `src/taxjson/lib/brokerages/questrade.py` — `scan_code_uses`, `_resolve_symbol`; `src/taxjson/lib/corp_actions.py` — `parse_questrade_corporate_actions`, `booked under Questrade's INTERNAL`

### Questrade: "row keeps internal symbol code 'X000009' (…) (not QZR.UN.TO: another kind of security: the code's descriptions state UNIT, the listing's name states none)" or "(… another share class: the code's descriptions state class A …)" for a spun-off code
- **Check:** the code's rows are a trust's or fund's (`QZR REAL ESTATE INVESTMENT TRUST UNITS DIST ON 100 SHS …`), or a spinoff leg whose wording names its parent's class (`QZX CORP SPINOFF ON 100 SHS FROM SEC# J0000002 QZP CORP CL A …`). `tjs --version` is a development build after v0.24.0. The later sale is in no total ("no purchase in your files").
- **Cause:** the designator check of a code (tax-logic CA-ACB-CODES / US-BASIS-CODES) read UNITS as a kind of security, and read a spinoff leg's parent name too, so a trust's distribution wording or the parent's class letter refused the right listing.
- **Fix:** upgrade: UNITS is no kind designator there (a SPAC unit is told apart by its warrant word and class letter), and a spinoff leg is read by its own name only. Meanwhile add `GLOBAL X000009.TO QZR.UN.TO` (the code's listing, then the real ticker) to `ticker.map`.
- **Fixed in:** `v0.24.1`
- **Code:** `src/taxjson/lib/symbol_codes.py` — `_KIND_MARKS`, `code_designators`, `questrade_own_name`, `designators_agree`

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

### "Warning: ATTENTION: 99900001.csv: RBC symbol QZOLD (USD) looks renamed to QZNEW" while a `.tt` line `RENAME <date> QZOLD.US QZNEW.US` already declares the change
- **Check:** `tjs renames` lists the change as an event booked from the `.tt` line (and `tjs renames --pending` does not suggest it), yet the run's "Reading" step still printed the hint, and the account's `.sum` DIAGNOSTICS kept it.
- **Cause:** the parsers (RBC, Questrade, Webull) dropped the hint for a pair ticker.map joins, but never saw the `.tt` RENAME lines, which the run reads apart from ticker.map. The hint's date may differ from the declared one (the new symbol's first row versus the company's change date): the pair is what counts.
- **Fix:** nothing to do: the run passes the declared changes to the parser (`work/declared_renames.list`), the console no longer shows the hint, and the `.sum` keeps a note instead: "note: 99900001.csv: RBC symbol QZOLD (USD) stops and QZNEW starts with the same description — the ticker change is answered by inputs/margin/renames.tt:3."
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/brokerages/base.py` — `set_declared_renames`, `declared_rename_where`, `answered_rename_note`; `src/taxjson/bin/taxjson_run.py` — `stage_declared_renames`; `src/taxjson/bin/taxjson_brokerage.py` — `--declared-renames`

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

### `run --fast` keeps the old result after an update that changed only taxjson's market data (no "Rebuilding everything: taxjson's code changed" line)
- **Check:** the update changed `src/taxjson/data/markets.toml` (a venue, an index-option root, a stablecoin, a split-share issuer) and no Python file; `tjs run` without `--fast` gives a different result from the `--fast` run before it.
- **Cause:** `--fast` trusts its cached stages only when they were built by the installed taxjson, but that check (the content fingerprint in `work/.code_fingerprint` and the modification-time scan) read only the package's `.py` files, so a change to the shipped data the code reads went unnoticed.
- **Fix:** upgrade: every shipped file the code reads counts, and the next `--fast` run says "Rebuilding everything: taxjson's code changed" and rebuilds. On an older install run `tjs run` without `--fast` once after updating.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_package_files`, `_package_fingerprint`, `_package_mtime`

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
- **Check:** the message names the loss, the purchase and the name both listings share, then the two ticker.map lines that answer it; `tjs ticker-map --suggest` offers the `TOBASE` line, and `tjs run --strict` stops on it. The loss is still allowed in `tjs sum`.
- **Cause:** two listings of one company's shares (the TSX line and the NYSE line) are identical property, but taxjson joins them only on evidence: a ticker.map `TOBASE` line, a `.tt` JOURNAL line or a transfer journal it pairs itself (tax-logic CA-XLIST-01), never on the names alone. Sold at a loss on one listing and bought on the other within 30 days, the unjoined pair kept the loss allowed with nothing said: no warning, no suggestion. Now a loss in the tax year with another listing of the same root bought within 30 days before or after it, in any of your accounts (registered included), under a name equal word for word once normalised, is flagged (CA-XLIST-05; US-XLIST-04 for a US project's wash sales). In Canada the other listing must still be held at the end of day 30, as the rule requires; the US rule has no such test. Names that differ, or a listing whose exports carry no name (a `.tt`-only book), are not flagged. A class share's root is also read without its class letter (a loss on `QZT.B.TO` and a purchase of `QZT`, both named "… CL B"); a split or consolidation of the other listing scales the units held at day 30. The run lists the first 20 pairs and counts the rest in one line ("3 more possible superficial losses across listings, not listed here"); `tjs ticker-map --suggest` lists every one.
- **Fix:** if the two listings are one security, add the `TOBASE` line (`tjs ticker-map --suggest --write` offers it) and `tjs run`: the loss is denied (disallowed) and its amount goes onto the replacement's cost. If they are different securities (a CDR, another company that uses the root), add the `DISTINCT` line. Either line ends the warning.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/xlist_loss_radar.py` — `analyze`, `open_findings`, `message`, `across listings`, `_roots`, `_held_at`, `RADAR_SHOWN`, `more_message`; `src/taxjson/bin/taxjson_run.py` — `_say_xlist_losses`; `src/taxjson/lib/ticker_map_suggest.py` — `from_xlist_loss_radar`

### No "possible superficial loss across listings" warning for a pair whose own broker names both listings alike (another account's export names one with other share wording), or the warning says "named 'QZX INC COM' and 'QZX INC SUBORD VTG SHS' — the same company; the names differ only in share wording"
- **Check:** a loss on one listing (QZX.US) and the other listing (QZX.TO) bought within 30 days, held at day 30, and no warning on v0.24.x; `tjs ticker-map --suggest` offers no `TOBASE` line for the pair. Another account's export (a registered account's Questrade rows, say) names one of the listings with its voting wording ("QZX INC SUBORD VTG SHS").
- **Cause:** the radar compared every name the project's exports gave either listing, and one name stating "SUBORDINATE VOTING" made the pair's names "not equal", so it was dropped with nothing said — although the broker of the loss and of the purchase named both listings the same.
- **Fix:** upgrade. The radar now judges the names of the loss's own rows and the purchase's own rows (their account and broker, matched by the export each row came from, an export saved under its account-number name too). Names that differ only in the voting-share phrase one of them ends with (a broker's style for an issuer with one listed class) are flagged too, said as differing in share wording — never when a name of either listing states a class letter or a second voting class, never for a depositary receipt (CDR) or two companies, and never for the same words inside a company's name ("NON STOP CORP" is not "STOP CORP": before the release such a pair stopped `run --strict`). Answer it as before: `TOBASE` if they are one security, `DISTINCT` if not.
- **Fixed in:** `v0.24.1`
- **Code:** `src/taxjson/lib/xlist_loss_radar.py` — `analyze`, `_scoped_names`, `_source_brokers`, `_wording_only`, `_voting_phrase`, `_named`, `differ only in share wording`; `src/taxjson/lib/cross_listings.py` — `shown_apart`

### `tjs ticker-map --suggest` lists "QZE.US and QZE.TO share their letters but the names are not equal … — verify" under "To verify" where QZE.TO and QZE.US are two companies that IB lists under one bare symbol
- **Check:** the IB statement's Financial Instrument Information lists two stocks under the symbol QZE (one on the TSX with a CA ISIN, one on the NYSE with a US ISIN); `tjs ticker-map --suggest` names QZE.US with both companies' names. The books are right: QZE.TO and QZE.US are two securities.
- **Cause:** the IB parser named a stock row from the first instrument the statement lists under its bare symbol, so a USD row of the NYSE company could carry the TSX company's name (and another statement, listing them the other way round, the right one): QZE.US had two names, and the check of same-root listing pairs could not tell the pair apart.
- **Fix:** upgrade and `tjs run`. When a statement lists several instruments under one symbol, a row takes the name of the one on its own listing's market (the Listing Exch, else the ISIN country), never the first listed; that check then reads the two as different companies and asks for no line. A `DISTINCT QZE.US QZE.TO` written to quiet it can stay (it changes no figure).
- **Fixed in:** `v0.24.1`
- **Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `_security_name`, `fii_all`; `src/taxjson/lib/cross_listings.py` — `gather`, `shown_apart`

### `tjs ticker-map --suggest` asks to verify QZX.US and QZX.TO ("… if they are one security add `TOBASE QZX.US QZX.TO` …; if not, `DISTINCT QZX.US QZX.TO`"), or `tjs tips` says "US-LISTING … hold QZX.TO instead", for a CDR or another company that uses the same letters
- **Check:** compare the two listings' names in your broker's exports: a CDR's name says so ("… CDR (CAD HEDGED)"), another company's name shares no company word (a real-estate trust on one venue, a currency ETF on the other). `tjs sum --json` is the same with or without a `DISTINCT` line for the pair.
- **Cause:** `tjs ticker-map --suggest` and `tjs tips` read every US and Canadian listing that share a root as a probable interlisting: the pair was listed to verify (asking for a `TOBASE` or `DISTINCT` line), and US-LISTING advised holding the Canadian line, even when the exports showed the Canadian line is a depositary receipt or the names are two companies'. The books were right (two securities); only the check nagged.
- **Fix:** upgrade. A same-root pair is still a candidate (interlisted shares usually keep their letters), but not when the exports show the two apart: the Canadian line is a depositary receipt (a receipt word such as CDR or ADR in its name, markets.toml `receipt_words`, or a listing on a venue that lists receipts) or the names share no leading company word (the cross-listing join's `companies_differ`; spaces and hyphens set aside, so "OPEN QZX CORP" and "OPENQZX CORP" or "QZ-TEL CORP" and "QZTEL CORP" are still a candidate, said "the names are not equal … — verify"). Such a pair needs no `DISTINCT` line. A pair's reason under "To verify" now says what the names show: "QZX.US and QZX.TO carry the same name ('…') but ticker.map does not join them — if they are one security add `TOBASE QZX.US QZX.TO` …; if not, `DISTINCT QZX.US QZX.TO`", or "… names not compared (no security name for QZX.TO) — verify, then …", or "… share their letters but the names are not equal … — verify"; US-LISTING adds "verify QZX.TO is the same security first" unless a ticker.map line or equal names prove it. `reports/crosslistings.rpt` follows the same rule, and `tjs ticker-map --suggest` no longer offers a parser's conditional `TOBASE`/`GLOBAL` hint ("Only if the position is really held under the other listing …") for such a pair. `DISTINCT` lines you wrote stay valid and still silence a pair; one written only to quiet a CDR or another company can go (it changes no figure).
- **Fixed in:** `v0.24.1`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_tips`; `src/taxjson/lib/map_hygiene.py` — `map_gaps`, `gap_reason`, `pair_verdict`, `listing_names`, `APART`, `carry the same name`, `names not compared`; `src/taxjson/bin/taxjson_lint_crosslistings.py` — `analyze`, `_pair_verdict`, `_listing_names`; `src/taxjson/lib/cross_listings.py` — `shown_apart`, `companies_differ`, `receipt_why`; `src/taxjson/lib/ticker_map_suggest.py` — `pending`, `_export_names`

### "Info: ticker.map:3: `DISTINCT QZX QZX.TO` is read as `DISTINCT QZX.US QZX.TO`" or "Warning: ticker.map:3: `TOBASE QZX QZX.TO` names QZX, a symbol the books do not hold"
- **Check:** the ticker.map line names one listing without its suffix (the broker's bare US ticker) and the other with one. On v0.24.0 and earlier nothing was said, and the `DISTINCT` line did not answer the cross-listing loss warning for QZX.TO and QZX.US (the warning and `tjs ticker-map --suggest` kept asking for the pair).
- **Cause:** the books spell every share listing with its suffix: a broker's bare US ticker (a Questrade or RBC USD row, an IB symbol) is `QZX.US`, and a bare symbol is a coin. The loss radar, the suggestions and the cross-listing join compared the line's symbols as written, so `QZX` matched nothing.
- **Fix:** upgrade. A `DISTINCT` line now also keeps the US listing of a bare ticker beside a share listing apart (it changes no symbol, so no pool moves; the pair as written stays, for a coin or a broker's code), with the Info line when the books hold the US listing and not the bare symbol; writing it `DISTINCT QZX.US QZX.TO` says the same. A `GLOBAL`, `TOBASE`, `JOURNAL` or undated `RENAME` line written that way is not re-read (it would join pools and change the gain on its own): write it as the Warning gives it (`TOBASE QZX.US QZX.TO`) and `tjs run`. A line whose bare symbol the books hold (a broker's internal code, a raw spelling the parser keeps) is live and nothing is said.
- **Fixed in:** `v0.24.1`
- **Code:** `src/taxjson/bin/taxjson_ticker_map.py` — `distinct_spellings`, `listing_spelling_notes`, `listing_pair_spelling`, `_parse_map_text`; `src/taxjson/lib/journals.py` — `distinct_spellings`; `src/taxjson/bin/taxjson_run.py` — `_say_listing_spellings`

### "Error: 1 .tt JOURNAL line(s) join two listings that nothing shows are one security" — "QZG.NE is written on a venue that lists depositary receipts" or "QZG.TO is named as a depositary receipt (CDR)"
- **Check:** the `.tt` line journals a Canadian depositary receipt (a CDR on Cboe Canada, written `QZG.NE`, or a listing the broker's export names "… CDR") and the US share it holds a fraction of (`QZG.US`). On v0.24.0 and earlier the line was booked: the two share the root QZG, which was taken as evidence that they are one security.
- **Cause:** a CDR is its own security (a receipt over a fraction of the share, usually currency-hedged), not a listing of the share; pooling the two would merge two costs. The books spell a Canadian venue `.TO`, so the venue is read from the line as written; `src/taxjson/data/markets.toml` marks Cboe Canada (`[venues.NE] receipts = true`) and the receipt words (`[lists] receipt_words`).
- **Fix:** a CDR is not journaled into its share: delete the line, and book a sale of one and a purchase of the other if that is what happened. If the two really are one security (both receipts, say), names that agree in the exports let the line book; or join them deliberately with a ticker.map `TOBASE` line. Two lines both written on the receipt venue (a Cboe Canada ETF's CAD and USD units, `JOURNAL … QZG.NE QZG.U.NE …`), or two names that both carry the receipt word inside the company's name (a company named "… SPONSORED HLDGS INC"; a word after the name, "… INC CDR", still counts), are not receipt evidence: a development build after v0.24.0 stopped on them; upgrade.
- **Fixed in:** `v0.24.1`
- **Code:** `src/taxjson/lib/cross_listings.py` — `receipt_why`, `declared_verdict`, `_hub_partners_agree`, `is written on a venue that lists depositary receipts`; `src/taxjson/lib/markets.py` — `receipt_suffixes`, `receipt_words`

### A broker journal split over several rows (one reference: 1000 out of QZD.TO, 600 and 400 into QZD.U.TO) is not joined, or its sale reads as missing history
- **Check:** `tjs transfers` shows three or more transfer legs in one account with one broker reference (RBC's `J~…`, Questrade's journal pair): the out-legs on one listing, the in-legs on the other, the same units in total. On v0.24.0 `tjs ticker-map --suggest` or `tjs journals` listed the move (or nothing), and `tjs find-missing-history` could flag the sale after it.
- **Cause:** three readers of a broker reference used three rules: the cross-listing join and the missing-history walk took a reference group as a journal only with exactly one leg each way, while the transfer-in check settled any group whose units balanced.
- **Fix:** upgrade and `tjs run`. One rule now (tax-logic CA-XLIST-04 / US-XLIST-03): a reference group is one journal when its out-legs name one listing, its in-legs one, they move the same units and every leg is within 5 business days of the others. Any other group (units that do not balance, a third listing, legs further apart) is no journal for any of them.
- **Fixed in:** `v0.24.1`
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

### "QZAX.US read as QZAX.TO: Questrade files the TSX listing on a USD row" for shares an account BOUGHT on a USD row (before release)
- **Check:** the account's own Questrade (or RBC) rows of the bare ticker include a purchase on a USD trade row, and the evidence the warning names is another account's ("account tfsa's QZAX.US was read as QZAX.TO on its own evidence").
- **Cause:** the same broker's proof in another account (GitHub issue #3) re-read every USD row of the ticker in a second account, a genuine USD purchase of the US listing too — while CA/US-XLIST-02 say a USD trade keeps the US listing.
- **Fix:** upgrade: that proof now re-reads only units that came into the account by a transfer or a dividend reinvestment (and their sales); an account that bought the ticker on a USD trade row keeps `ROOT.US`. If those shares really are the TSX listing, write `TOBASE QZAX.US QZAX.TO` in `ticker.map`.
- **Fixed in:** `v0.24.1`
- **Code:** `src/taxjson/lib/listing_suffix.py` — `resolve`, `Candidate`, `scan_questrade`, `scan_rbc`

### A correct `TOBASE QZB.US QZA.TO` for a move from IB to Questrade left "Info: 1 position(s) go short in acct's data (QZA.US)" (in a registered account "Warning: Short position: QZA.US …"), or a QZA.US position the broker never held
- **Check:** `tjs transfers` shows QZB.US out of IB and QZA (the company's TSX root) into Questrade on a USD row the same week, under one name; the account also holds QZA.TO in CAD; ticker.map has `TOBASE QZB.US QZA.TO`; `tjs journals` lists the pair as refused by your ticker.map.
- **Cause:** the map's line booked the out-leg as QZA.TO, but the in-leg kept the row currency's listing QZA.US (the account holds QZA.TO in CAD, and one native-currency pool cannot hold both), and the transfer pairing refused the pair because the map names the out-leg. The units left QZA.TO and arrived in QZA.US, a different security: in a registered account a withdrawal at fair value, in a taxable one a later sale read as a short with no purchase (in no total), with nothing on the console. Without the map line the pair was joined as QZB.US and QZA.US.
- **Fix:** upgrade and `tjs run`. The map's renames apply to a transfer's out-leg before the listing and the pairing are read; an out-leg the map books as the in-leg's other listing joins the in-leg to that listing in the base-currency books: "Warning: acct: joined as one security by their transfer journal: QZB.US ↔ QZA.US (transfer 2026-09-01; ticker.map books QZB.US as QZA.TO, so QZA.US joins QZA.TO)" (tax-logic CA-XLIST-01 / CA-XLIST-02, US-XLIST-01 / US-XLIST-02). The join keeps the base currency's listing (in a US project `TOBASE QZA.TO QZA.US`, the map's own line chained on), unless the map already renames that listing. On an older install add `TOBASE QZA.US QZA.TO` to ticker.map (`tjs ticker-map --suggest` offers it).
- **Fixed in:** `v0.24.1`
- **Code:** `src/taxjson/lib/listing_suffix.py` — `resolve`, `which ticker.map books as`; `src/taxjson/lib/cross_listings.py` — `analyze`, `map_renames`, `joined_note`; `src/taxjson/bin/taxjson_run.py` — `_ticker_map_renames`, `_listing_suffix_text`, `stage_cross_listings`

### "Warning: acct: ticker.map books the two legs of a transfer as two securities: 24 QZB.US out 2026-09-01 (booked as QZZ.TO) / QZA.US in 2026-09-03"
- **Check:** `tjs journals` lists the pair as refused by your ticker.map; the line naming QZB.US (or QZA.US) books it as a symbol the other leg is not booked as.
- **Cause:** the two legs pair as one move (the same quantity, within 5 business days), but the map's lines book them as two different symbols, so neither leg pairs: the units leave one security and arrive in another (in a registered account a withdrawal at fair value; the in-leg's position has no cost carried). Earlier releases said nothing.
- **Fix:** if they are one security, add the line the Warning names (`TOBASE QZA.US QZZ.TO`) or correct the line naming the other leg; if they are two securities, add `DISTINCT QZB.US QZA.US`. `tjs run --strict` stops until one of them is in ticker.map. The Warning is only for legs whose names agree, and not for a pair a `DISTINCT` line keeps apart (the legs or the symbols they are booked as): a development build after v0.24.0 also warned (and `--strict` stopped) on an in-leg with no name that only shared a quantity and a date with an unrelated transfer.
- **Fixed in:** `v0.24.1`
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
- **Fixed in:** `v0.24.1`
- **Code:** `src/taxjson/lib/brokerages/rbc_direct.py` — `reinvest_row_price`, `reinvest_identity_error`; `src/taxjson/lib/brokerages/questrade.py` — `reinvest_row_price`; `src/taxjson/lib/brokerages/schema.py` — `is far `

### Questrade: "Warning: Short position: QZP.TO (lira): a registered account (TFSA/RRSP) cannot be short" after a dividend reinvestment (REI) on a USD row
- **Check:** `tjs shares` (or the parsed book) shows the account `+1 QZP.US` and `-1 QZP.TO` for one share: the REI row has the bare TSX ticker (`QZP`) and Currency `USD` (Questrade pays the dividend on the USD side), and a later sale of that share is on a CAD row. On v0.24.0 the parser also said "'QZP' is booked under its own symbol, but the same security … trades as QZP.TO".
- **Cause:** the reinvested share took the row currency's listing (`QZP.US`, which may be another company's NYSE ticker), while the dotted `.QZP` dividend it reinvests bound to the account's held listing `QZP.TO`. Nothing is missing: the purchase sat one listing over.
- **Fix:** upgrade and `tjs run`. A reinvestment of a bare ticker on the other currency's row now books the listing the account trades under the same name and root (its cost stays the row's cash), and an account with no such holding takes the listing another account's evidence proved for the same broker and name (tax-logic CA-XLIST-02 / US-XLIST-02). With neither, the row currency's listing stays (a DRIP of a US stock).
- **Fixed in:** `v0.24.1`
- **Code:** `src/taxjson/lib/brokerages/questrade.py` — `_rei_listing`, `_resolve_symbol`; `src/taxjson/lib/listing_suffix.py` — `resolve`, `project_evidence`, `scan_questrade`, `proved`

### "Warning: raw holdings skipped for 'lira': QZP.TO would pool mixed currencies" after a dividend reinvestment, then `tjs tips`: "no holdings reports"
- **Check:** the account's Questrade export has an REI row with the bare TSX ticker on a USD row (`QZP`, `REINV@C$…`) and the account trades `QZP.TO` in CAD. `reports/<account>_holdings.toml` is missing or old. The totals are right.
- **Cause:** a development build after v0.24.0 booked the reinvestment on the held CAD listing (the entry above) but kept its USD cash, so the native-currency books (which never convert) held one pool in two currencies and were skipped.
- **Fix:** upgrade: the row now carries its listing's currency (`listing_currency`) and the native books restate it at the day's rate, like a foreign-currency return of capital; the run prints a `note:` line saying so. With no rate on file for that day the view is still skipped: refresh the rates (`tjs run` without `TAXJSON_OFFLINE`).
- **Fixed in:** `v0.24.1`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_raw_align_adjust_currency`, `_raw_mixed_currency_symbols`, `would pool mixed currencies`; `src/taxjson/lib/brokerages/questrade.py` — `_rei_listing`, `listing_currency`; `src/taxjson/lib/core.py` — `TaxTransaction`

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

### `tjs fetch`: an empty `questrade_2025.csv` (or `ib_flex.csv`, or Questrade token file) after two fetches ran at once, "FileNotFoundError: … questrade_2025.csv.part", or "Questrade auth failed" right after another fetch succeeded
- **Check:** two `taxjson fetch` commands overlapped: two terminals, a scheduled fetch, or two projects fetching Questrade at the same time (they share `~/.questrade_token`).
- **Cause:** the fetch plugin staged every write in one fixed `<file>.part`: the second writer replaced the first one's temp file, so the first rename published the second's unfinished (empty) file and the second rename failed. Two fetches could also both read the Questrade token before either saved the rotated one; each refresh kills the token it used, so the second refresh failed, or two fetches of one project merged into the same CSV and the last write lost the other's rows.
- **Fix:** upgrade: each write has a temp file of its own, one fetch runs per project at a time (a second one says "waiting for another `taxjson fetch` in this project to finish" and waits as long as the first runs — Ctrl-C stops it), and the token's read, refresh and save happen under a lock beside the token file. A symlink at a lock file's name is replaced by a lock file of its own (never followed); if it cannot be, the fetch stops with "… is a symlink and could not be removed" instead of running unlocked. On an older release, run one fetch at a time; re-fetch a file left empty (the next fetch re-covers the window); after a dead token, start a new chain with `tjs fetch --refresh-token <new token>`.
- **Fixed in:** `v0.25.0`
- **Code:** `packages/taxjson-fetch/src/taxjson_fetch/api.py` — `write_private`; `packages/taxjson-fetch/src/taxjson_fetch/command.py` — `run`, `_qt_open_session`, `_questrade_token_write`; `src/taxjson/lib/safe_write.py` — `file_lock`, `LockLinkError`, `write_atomic`

### `tjs fetch --trim-overlap`: the original CSV was copied outside the project, to where an `<export>.bak` symlink pointed
- **Check:** `ls -l inputs/<account>/` shows `<export>.bak ->` a path, and the file it points at holds the full original export.
- **Cause:** the backup name was chosen with a test that is false for a dangling symlink, so a link at `<export>.bak` was taken as a free name and the copy was written to the link's target.
- **Fix:** upgrade: a symlink at a `.bak` name, dangling or not, is skipped and the backup goes to the next free `<export>.bakN`, written owner-only; the console line names the backup. The previous IB Flex statement's backup is kept the same way. On an older release, remove the `.bak` link before `--trim-overlap` and delete the outside copy.
- **Fixed in:** `v0.25.0`
- **Code:** `packages/taxjson-fetch/src/taxjson_fetch/command.py` — `_qt_trim_file`; `src/taxjson/lib/safe_write.py` — `backup_copy`

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

### The first run of a recent project downloads rates back to 2000: "Info: FX USD→CAD: Bank of Canada Valet for 2,400 dates, Yahoo fallback for 1,322 (2003-…)"
- **Check:** the project's exports start years later than those dates; `head -1 work/to_base.csv` shows the first date fetched.
- **Cause:** the rates stage runs before the exports are parsed, so it asked for every date from 2000-01-01, and Yahoo Finance for the years before the Bank of Canada's series.
- **Fix:** upgrade: the window starts a few days before the earliest date in the project's exports, `.tt` files, slips and positions snapshots (never later than January 1 of the year), recorded in `work/.to_base.start`; an export reaching further back widens it on the next run.
- **Fixed in:** unreleased
- **Code:** `src/taxjson/lib/rates_window.py` — `window_start`, `earliest_in_text`; `src/taxjson/bin/taxjson_run.py` — `_rates_inputs`, `RATES_START_STAMP`

### "taxjson-to-base-curr needs the [fx] extra for a USD target (Yahoo Finance)" then "Error: stopped at fetching currency rates (CAD, USD) (exit 1)", with `TAXJSON_OFFLINE=1`
- **Check:** the project is `base_currency = "USD"` (a US project), `TAXJSON_OFFLINE` is set, and `pip show yfinance` finds nothing (taxjson installed without the `[fx]` extra). The run printed "Loading cached CAD → USD rates" just before.
- **Cause:** a USD target's rates come from Yahoo Finance, so the rates helper refused to start without the `[fx]` extra, even offline, where it reads `~/.currency_price_cache.json` only and never calls Yahoo.
- **Fix:** upgrade: offline, the helper reads the cache without the extra (a date the cache does not cover still stops the conversion, as in the entry above). Online, a USD target still needs the extra: re-run the installer with `TAXJSON_EXTRAS=fx`, or from a checkout `pip install -e '.[fx]'`.
- **Fixed in:** `v0.26.1`
- **Code:** `src/taxjson/bin/to_base_curr.py` — `main`, `needs the [fx] extra`, `offline_enabled`

## Options

### "Warning: 1 option contract you wrote in 2025 and closed in 2026 is on transition close timing: 400.00 of premium is taxed in 2026"
- **Check:** `tjs option-boundary` lists the contract with "QUESTION: did your 2025 return report the … premium when the contract was written?"; `tjs checklist` shows `[!]` (or `[>]`, the next step) on option-boundary with the same question. The project's `[settings] option_grant_timing_since` is the project year (what `tjs init` writes) and no `filed/2025.json` lock (or `prior_year_record`) records how 2025 was filed.
- **Cause:** grant timing (ITA s.49(1)) puts a written option's premium in the year written, but a contract written before `option_grant_timing_since` keeps close timing (the transition from books filed the old way): its premium is in this year's gain at the buy-back or expiry. That is right only if the write year's return did not report the premium; if it did (last year's return was on grant timing — a previous taxjson project, or your preparer's), the premium is taxed twice. Only you know which; nothing was said before (the checklist showed "no amendment required").
- **Fix:** if last year's return reported these premiums when written, set `option_grant_timing_since = 2025` (the first year filed under grant timing) and keep it in every later project: the premium then stays in 2025 and only the buy-back is 2026's. If it did not, the transition is right: `tjs checklist --done option-boundary` (add `--note`) answers it, and the run says a note instead. A `filed/2025.json` lock that records the timing answers it too.
- **Fixed in:** `v0.24.1`
- **Code:** `src/taxjson/lib/option_boundary.py` — `straddling`, `QUESTION: did your`, `project_question_rows`, `question_message`; `src/taxjson/bin/taxjson_run.py` — `_say_option_transition`, `_checklist_answered`; `src/taxjson/lib/checklist.py` — `d_option_boundary`

### "Warning: margin: QZQ250117C00040000.US expired 2025-01-17 but the books still hold 1 (long)"
- **Check:** `tjs list margin` shows the contract still open after its expiry.
- **Cause:** the export is missing the expiry, assignment or exercise row. Variants of the warning say the contract was booked under another root (a ticker.map `GLOBAL` line joins them), or that the broker coded the opening trade CLOSING (a missing write or purchase from before the data).
- **Fix:** add the missing row (an expiry is a `.tt` BUYSELL closing the position at 0 on the expiry date), or the ticker.map line the warning prints, and re-run.
- **Fixed in:** `v0.17.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_warn_expired_open_options`, `the export is missing its expiry, `

## Before you file

### `tjs sum`: "FX on foreign cash: NOT RELIABLE for 2025 — 3 in-year overdrafts (412.50 USD); conversions, deposits/withdrawals and margin balances are not read; do not file this figure"
- **Check:** `tjs fx-cash` prints the same line first; `tjs fx-cash --json` has `"reliable": false` and the raw figures under `unreliable_raw`; `tjs checklist` shows `[!] fx-cash` with it. The project has no `[settings] fx_cash_ledger`, or it is `"v1"`.
- **Cause:** the default FX-on-cash ledger (s.39(1.1), US §988) is rebuilt from the trades and income of the taxable books only. It never sees a currency conversion (IB Forex trades, Kraken or Coinbase fiat trades, a bank's), a deposit or withdrawal, a foreign-currency margin balance or the pool carried from the year before, so each spend it cannot cover is an "overdraft" moved at the day's rate with no gain, and the result can be wrong in either direction. Before this release `tjs sum` showed its figure as "reportable … line 15300" with a caveat.
- **Fix:** do not file that figure. Work out the line 15300 FX result from the statements, or try the opt-in ledger v2 (under audit): `tjs fx-cash --ledger v2` reads the conversions, deposits/withdrawals and statement balances, and says what it computed or, line by line, what it needs (next entry). `tjs checklist --skip fx-cash --note "..."` records that you handled it yourself.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/bin/taxjson_fx_cash.py` — `unreliable_status`, `NOT_READ`; `src/taxjson/bin/taxjson_run.py` — `_fx_cash_status`, `_fx_sum_item`, `cmd_fx_cash`; `src/taxjson/lib/checklist.py` — `d_fx_cash`

### `tjs fx-cash --ledger v2`: "FX on foreign cash: NOT COMPUTED for 2025, ledger v2 (opt-in, under audit) — 4 problems, first: no opening pool for margin/ib USD … ; no reportable figure"
- **Check:** the report's NOT COMPUTED table lists each problem with its date, account (book) and amount, and the lines below it say what to add; `--cash-events` lists every conversion, move and balance the ledger read.
- **Cause:** ledger v2 refuses instead of guessing: an account holds foreign cash at the start of the year with no cost (`opening`), a deposit or withdrawal nothing declares (`undeclared`), a statement balance the ledger does not reach within 1.00 (`reconcile`: a conversion or move it does not see), an account with activity and no balance to check against, an overdraft in an account that does not reconcile, a move between your own accounts that arrives before it leaves (`own`), a missing FX rate, or an account of a broker whose export it reads no conversion, deposit or withdrawal from (`unread`: Webull, the generic importer).
- **Fix:** add the `.tt` lines it names to a `.tt` file of the account's `inputs/` folder (docs/settings.md, `.tt` files: `CASHOPEN` once for the first year, `CASHMOVE … cost=` / `kept` / `proceeds=` / `own` / `spot` per move, `CASHBAL` for an export without balances, `CASHBOOK <book>` in a `.tt` file of a folder holding several broker accounts, `CASHBOOK <book> complete` once a Webull or generic account's conversions and moves are all lines), then `tjs fx-cash --ledger v2` again; `fx_cash_inflow_cost = "spot"` takes the day's rate for undeclared deposits. Close the year with `fx_cash_ledger = "v2"` and the next year opens from the recorded pool.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/fx_cash_v2.py` — `build`, `headline`, `TOL`; `src/taxjson/lib/cash_events.py` — `parse_line`, `collect`, `Books`

### `tjs fx-cash --ledger v2` computed a Questrade account's figure as a margin loan ("BORROWED"), or: "wb/webull: taxjson does not read webull's conversions, deposits or withdrawals from its export"
- **Check:** `tjs fx-cash --ledger v2 --cash-events` lists what the ledger read for the account; the report's NOT READ FROM THE EXPORT table names the accounts whose exports give it nothing.
- **Cause:** ledger v2 read no cash event from a Questrade, Webull or generic export, so an account that converted Canadian dollars before buying a US share read as one that borrowed them, and a figure was computed on it.
- **Fix:** upgrade: Questrade's FX conversions (FXT), deposits, withdrawals, cash-only transfers and stock-lending income are read. For Webull and the generic importer write each conversion and move as a `.tt` line (`FXCONV`, `CASHMOVE`) in the account's folder, then `CASHBOOK <book> complete` (e.g. `CASHBOOK webull complete`) to say they are all there.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/brokerages/questrade.py` — `questrade_cash_events`; `src/taxjson/lib/cash_events.py` — `READERS`, `unread`; `src/taxjson/lib/fx_cash_v2.py` — `unread_books`

### "Error: m.tt:4: malformed CASHBAL line — … — a line of the FX-on-cash ledger v2 only"
- **Check:** the line named is a `FXCONV`, `CASHMOVE`, `CASHOPEN`, `CASHBAL` or `CASHBOOK` line.
- **Cause:** the cash lines are read only by the opt-in ledger v2, but a malformed one stops `tjs run` under either ledger (a typo must not vanish).
- **Fix:** correct the line to the form the message shows, or delete it if you do not use ledger v2.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/bin/taxjson_convert_tt.py` — `a line of the FX-on-cash ledger v2 only`

### `tjs elect` says "No elections recorded: lira" although the run booked a spin-off there ("sheltered account lira: spin-off … booked at $0 cost")
- **Check:** `tjs spinoffs` lists the event with election `sheltered_default`.
- **Cause:** the sheltered default books the event without saving an election, and the listing read only the saved ones.
- **Fix:** upgrade: `tjs elect` lists each such event as "sheltered default ($0 cost for the distributed shares)" (a merger: "the old shares' cost carried"); `tjs elect ACCOUNT --set ID=ELECTION` records another treatment.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_defaulted_events`, `_elections_section`

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

### "warning: margin_gains_wash.json was built before cash_base.json changed — wash-adjusted numbers are stale", or no such warning for an account after `tjs run --account` rebuilt ANOTHER taxable account
- **Check:** a `tjs run --account <one account>` ran after the last full `tjs run`. The file names in the warning say which books changed since the cross-account pass: another taxable account's `_base.json` / `_gains.json`, `sheltered_base.json` or `loss_overrides.json`. `tjs checklist` puts audit and form-export at attention ("older than their inputs") and `tjs close-year` stops ("are STALE").
- **Cause:** a taxable account's wash-adjusted numbers come from one pass over every taxable account (Canada pools the cost of identical property across them and a buy in one can make a loss in another superficial; the US matches wash sales across them). `run --account` skips that pass. The check used to compare only the account's own books, so after a rebuild of another account the old blended numbers were served without a warning.
- **Fix:** run a full `tjs run` (no `--account`) before using or filing any figure. Since the fix, the pass records each member's books and a fingerprint of each, and a change in any of them marks every account of that pass stale — judged by that fingerprint for the account's own books too, so a `tjs run --account` of the account itself that rebuilds the same bytes does not warn; on an older release, run the full `tjs run` after any `--account` run.
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/lib/report_model.py` — `stale_wash_inputs`, `record_wash_inputs`, `WASH_INPUTS_FILE`, `resolve_gains_files`; `src/taxjson/bin/taxjson_run.py` — `_record_wash_inputs`, `stage_blended_wash_pass`

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

### A superficial loss (US: a wash sale) taxjson denies that I want to claim: a replacement inside day 30 counted from the settlement date, outside it from the trade date
- **Check:** `tjs wash-sales` lists the denial and its replacement; `tjs edge-cases` marks a replacement a few days from day 30 THE DATE BASIS DECIDES THIS ONE. In Canada the window is counted on settle dates whatever `tax_date` says (CA-SL-01); in the US on trade dates (US-WASH-01).
- **Cause:** the engines apply the rule as the law's mechanical test, black and white. Taking a position against one denial is a filing decision only you (and your adviser) can make; taxjson never infers it.
- **Fix:** add a line to a `.tt` file of the taxable account that sold: `ALLOWLOSS 2025-12-19 QZA.TO reason="the RRSP call was bought 32 days after the trade date"` (the sale's trade or settlement date, its symbol as `tjs wash-sales` spells it, and its units when two denied sales of the symbol share the day), then `tjs run`. The loss stays allowed and no ACB (US: basis) is raised for it; every run says so in one Warning, `tjs sum` lists it under FILING POSITIONS with the denial the rule would make, and the checklist's filing-positions step stays manual until you mark it done. `tjs form-export` notes the position on the sale's row (US: no code W), and `tjs audit`, `tjs wash-sales --explain`, `tjs carryover` and `tjs handoff` name it. Delete the line to apply the rule again. If the run stops with "Error: 1 .tt ALLOWLOSS line(s) name no single denied superficial loss", the line's date or symbol matches no denied sale (the message lists that day's trades in the account) or matches two (add the units sold).
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/loss_overrides.py` — `parse_line`, `problems`, `warning_message`; `src/taxjson/bin/taxjson_run.py` — `_read_loss_overrides`, `_say_loss_overrides`, `name no single`; `src/taxjson/lib/checklist.py` — `d_filing_positions`

### "Error: 1 .tt ALLOWLOSS line(s) name no single denied superficial loss" with "names the same sale as inputs/margin/m.tt:3" or "60 units is one fill of the 100-unit sale"
- **Check:** the message names both lines, or the sale's units; `tjs wash-sales` lists the denied sale.
- **Cause:** a line names a WHOLE sale. Two lines naming one sale in different spellings (one with the units, one without; one by the trade date, one by the settlement date) are one position taken twice. The units, when given, are the sale's total: one same-day sell-down in several fills is one sale, and one fill's units used to override the whole sale.
- **Fix:** keep one line per sale; write the sale's total units, or none. A `reason="..."` written after a `#` is part of the comment: put the reason before it.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/loss_overrides.py` — `problems`, `plan`, `parse_line`, `_comment_start`

### "Error: 1 .tt ALLOWLOSS line(s) name no single denied superficial loss" — "no denied loss matches QZL.TO sold 2024-12-16 in account margin; that day's trades in account margin: …" and the sale is not in the list
- **Check:** `tjs wash-sales` spells the denied sale another way: the other listing of the same root (`QZL.US` where the line says `QZL.TO`, with no `TOBASE` line joining the two), the symbol a ticker.map rule books it under, or a date a few days off (the trade date where the line has a settlement date the books do not, or the other way round).
- **Cause:** the line names a sale by the symbol and date the books carry. The message listed the day's trades in the engine's order and cut the list at twelve, so on a busy day the sale the line meant could be cut away, and it never said which spelling the books use.
- **Fix:** upgrade: the day's trades of the line's root, on any listing, come first and are never cut, and the message says how the books spell the sale — "the books spell this sale QZL.US — write `ALLOWLOSS 2024-12-16 QZL.US ...`, or if the two listings are one security add `TOBASE QZL.US QZL.TO` to ticker.map", "ticker.map books QZO.US as QZN.US — write …", or "the books have a sale of QZL.TO traded 2024-12-13, settled 2024-12-16 — write `ALLOWLOSS 2024-12-13 QZL.TO ...`". Correct the line (or add the `TOBASE` line if the listings are one security: the denial then sits on the joined symbol) and `tjs run`.
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/lib/loss_overrides.py` — `spelling_hint`, `_day_trades`, `listing_root`, `NEAR_DAYS`, `map_view`, `the books spell this sale`; `src/taxjson/bin/taxjson_run.py` — `_say_loss_overrides`

### `tjs form-export` (or `tjs audit`) shows an ALLOWLOSS sale as an ordinary loss: no note, "disallowed 0.00", and `tjs wash-sales --explain` says "no matching gains found"
- **Check:** `tjs sum` lists the sale under FILING POSITIONS.
- **Cause:** the return forms and the per-sale traces did not read the position the run recorded on the sale's rows (`loss_override`), so a claimed loss the rule would deny looked like any other loss.
- **Fix:** upgrade. The Schedule 3 row's notes and the Form 8949 part say "filing position: ALLOWLOSS inputs/margin/m.tt:4, the superficial-loss rule would deny …" (`--json`: `filing_positions` / `filing_position`; `--csv`: the notes / note column; `--form txf` warns, its record carries no wash-sale amount); `tjs audit` has a POSITION line, `tjs wash-sales --explain` traces the sale, the US `tjs wash-radar` reads the loss as claimed. US: the 8949 row has no code W and nothing in (g); if the 1099-B reports box 1g for that sale, docs/tax-rules.md (US-WASH-25) says how the row differs.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/bin/taxjson_form_export.py` — `_filing_position`, `FILING_POSITION_NOTE_8949`; `src/taxjson/bin/taxjson_audit.py` — `loss_override`; `src/taxjson/lib/trace_format.py` — `filing_position_text`; `src/taxjson/bin/taxjson_wash_radar.py` — `_us_engine_losses`

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

### `tjs slip-audit`: "Capital-gains dividends  T5 18 … differs" (or `tjs checklist` step `t5-t3`: "margin: 1 finding(s), e.g. margin (U5***) CAD: capital-gains dividends (T5 18) slip 20.00, books 0.00")
- **Check:** `tjs slip-audit` shows the line, and under Suggestions a `[[capital_gains_dividends]]` table naming the payment (from IB's dividends report, or a `[[slip.line]]` with box 18 in `inputs/slips/slips.toml`).
- **Cause:** a split-share or mutual-fund corporation paid part of a dividend as a capital-gains dividend (T5 box 18, line 17400). No export says so, so the books carry it as a dividend until taxjson.toml names it (tax-logic `CA-INC-06`).
- **Fix:** add the suggested table to taxjson.toml and re-run `tjs run`; `tjs divs-sum` then shows it apart. A payment in lieu's box-18 part cannot be named (the table covers dividends only): it stays in the difference, said in the Notes.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/slip_audit.py` — `_suggest_cgd`, `_cgd_entry`; `src/taxjson/lib/cg_dividends.py` — `allocate`

### `tjs slip-audit`: "Return of capital  T3 42 … differs"
- **Check:** `tjs slip-audit` lists the T3's return of capital and, under Suggestions, `.tt` lines for `inputs/<account>/slip-audit.tt`; `tjs roc-sum` shows no ACB reduction for the fund.
- **Cause:** the fund's T3 returns capital (box 42) that the export booked as part of the dividend (IB's statement carries the whole payment as one dividend), or a T3 issued after the year.
- **Fix:** add the suggested lines to the `.tt` file and re-run: `ADJUST … type=roc` lowers the ACB, and a `DIVIDEND` line with a negative amount takes the return of capital out of the dividend income when the dividend row holds it. A Canadian trust's line carries its record date (`record=`) so it counts in the T3's year (`CA-INC-DATE-ROC-TRUST`).
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/slip_audit.py` — `_suggest_roc`

### `tjs slip-audit`: "Not compared: inputs/slips/U5***.2025.dividends.csv: no taxable account's books carry IB account U5***"
- **Check:** the IB statement for that account is in `inputs/<account>/` and `tjs run` has run since.
- **Cause:** the report is matched to the taxable account whose books carry the IB account; a registered account (it gets no T5/T3), or no IB statement for the account in the project, leaves no match.
- **Fix:** add the IB statement to the account's folder, or name the account in `inputs/slips/slips.toml`: `[[ib_report]]` with `file = "<the report's name>"` and `account = "margin"`.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/slip_audit.py` — `audit`, `find_ib_reports`; `src/taxjson/lib/ib_dividends.py` — `read_report`

### `tjs slip-audit`: "margin: Canadian dividends … from rbc.csv is on no slip" (or "… and no T5/T3 slip for it")
- **Check:** the Coverage section names the account and the input files the income came from.
- **Cause:** the account (or one broker account in it) has dividends, foreign income, withholding or return of capital in the books and no slip in `inputs/slips/`. Slips that name a `broker_account` cover only that broker account's rows.
- **Fix:** type the missing slip into `inputs/slips/slips.toml` (`tjs slip-audit --template` prints one per account and currency). Interest alone under 50 is not a gap: no T5 is issued for it (it is still income).
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/slip_audit.py` — `_audit_account`, `_income_by_source`

### `tjs slip-audit`: "Error: inputs/slips/slips.toml [[slip]] #2: …"
- **Check:** the message names the slip table and the key (`account is required`, `not a T5 amount box`, `an identifier, not an amount`, `'405 54' is not an amount`, `year = 2024, but the project's year is 2025`).
- **Cause:** a slip typed in a way slip-audit cannot read: an unknown key or box, an account that is not in taxjson.toml or is registered, an amount with a space or a decimal comma, last year's file.
- **Fix:** correct the table as the message says (format: `docs/settings.md`, "inputs/slips/"). Never type a name, a SIN or an account number into `boxes`.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/slip_audit.py` — `load_slips_file`, `_slip_from_table`, `SlipsError`

### `tjs slip-audit --import-cra`: "Not imported: 2025 T5 Sample Bank.pdf: no broker in the books by the issuer's name"
- **Check:** the slip's issuer is a bank or a broker none of the project's exports come from, or the broker's name on the CRA slip is not the one taxjson knows it by (a trade name).
- **Cause:** a CRA copy shows no account number: the importer places a T5 by its issuer's name and the payments in the books. The issuer must carry a broker's whole name (every word of it but a legal form like INC.), or be a carrying dealer `src/taxjson/data/slip_issuers.toml` names for a broker the books hold: "TD DIRECT INVESTING", "RBC ROYAL BANK" or "RBC GLOBAL ASSET MANAGEMENT" is not RBC Direct Investing (before, a shared word such as DIRECT or RBC placed it there). A bank account's interest is outside the books.
- **Fix:** import that PDF again naming the account it belongs to: `tjs slip-audit margin --import-cra "<file>" --write` (it is then compared with the account's rows no other slip's broker account holds), or leave it out and report it from the slip.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/cra_slips.py` — `place`, `broker_of_issuer`

### `tjs slip-audit --import-cra`: a Webull T5 "from CI INVESTMENT SERVICES INC./CI SERVICES D'INVESTISSEMENT INC" is not imported: "no broker in the books by the issuer's name"
- **Check:** the slip line of the PDF names CI Investment Services, the project has a Webull export in an account, and `tjs --version` is older than the fix.
- **Cause:** Webull Canada's accounts are carried by CI Investment Services, which issues their T5 slips: the issuer does not carry the name "Webull", and the importer took only an issuer carrying a broker's whole name. And Webull's export books trades only: with no Webull payments in the books, the Webull broker account was no candidate either.
- **Fix:** upgrade. The carrying dealers ship as data, `src/taxjson/data/slip_issuers.toml` (an `[[alias]]` per broker: "CI INVESTMENT SERVICES" or its French half "CI SERVICES D'INVESTISSEMENT" is Webull's; CI Direct Investing and other CI businesses are not), and a broker account whose export has no income rows is that broker's (when none of its accounts has payments in the books): the slip goes to the account holding the Webull export. `tjs slip-audit` then shows it under the Webull export with "has no income in the books": the export has no dividends, so enter them as `.tt` DIVIDEND lines. Before upgrading, import the PDF naming its account: `tjs slip-audit margin --import-cra "<file>" --write`. A project without a Webull export does not place it (the message names Webull).
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/lib/cra_slips.py` — `issuer_aliases`, `alias_of_issuer`, `broker_of_issuer`, `_bare_groups`; `src/taxjson/lib/slip_audit.py` — `broker_account_files`

### `tjs slip-audit --import-cra`: "its Webull broker account cannot be told: the books hold 2 and no 2025 payments in them to match"
- **Check:** the books hold two or more broker accounts of the slip's broker and none has dividends, withholding or interest in the year (an export of trades only, such as Webull's).
- **Cause:** a CRA copy shows no account number, and the slips of one broker are shared out among its accounts by the books' payments: with none, any choice is a guess, so the slip is listed instead.
- **Fix:** import it again naming its account (`tjs slip-audit margin --import-cra "<file>" --write`: it goes in that account with no broker account), or type it into slips.toml with `broker_account`.
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/lib/cra_slips.py` — `place`

### `tjs slip-audit --import-cra`: "ambiguous: ZZF.TO and ZZF.TO distributions match it equally" or "no fund in the books whose distributions match its amounts or name"
- **Check:** `tjs divs ZZF.TO` lists the fund's distributions in each broker account; compare with the T3's boxes (21, 23, 25, 26, 49 and 42).
- **Cause:** a T3 is placed in the fund whose year's distributions add up to the slip (with or without its return of capital), or whose descriptions carry its name. Two holdings of one fund with the same total, or distributions the books lack, leave it unplaced.
- **Fix:** type that T3 into `inputs/slips/slips.toml` with its `security` and `broker_account` (docs/settings.md), or add the missing export.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/cra_slips.py` — `place`

### `tjs slip-audit`: "Not compared: margin: ZZF.TO ADJUST 2025-01-08 (roc.tt) has no record= and is dated by its pay date, but the distribution paid 2025-01-08 has record date 2024-12-31"
- **Check:** the `.tt` return-of-capital line was typed from last year's T3 (box 42) with the January pay date.
- **Cause:** without `record=` the line is dated by its pay date and lowers the ACB in this year; the distribution it belongs to is last year's by its record date (CA-INC-DATE-ROC-TRUST). Before, slip-audit netted it against this year's T3 and suggested a wrong line.
- **Fix:** add `record=2024-12-31` (the record date the message names) to the line and re-run `tjs run`.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/slip_audit.py` — `_redate_prior_roc`

### `tjs slip-audit`: "ZZT.TO: the books already hold ADJUST 2025-09-30 3.45 CAD (roc.tt), not counted against this slip"
- **Check:** `tjs roc-sum` lists the line; the fund is held at two brokers in one account (or the line's date puts it in another year).
- **Cause:** a hand-entered row has no broker account; it goes with the fund's distribution on its record date or pay date, else with the one slip showing its amount. When that cannot decide, the row is counted against no slip — and slip-audit never suggests a line the books already hold (before, it suggested it again: applying it booked the return of capital twice).
- **Fix:** give the line its record date (`record=`) or type each broker's T3 with its `broker_account`, so the row is placed; do not add the line again.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/slip_audit.py` — `_audit_account`, `_roc_match`, `_suggest_roc`

### `tjs slip-audit --import-cra`: "Error: pdftotext is not installed"
- **Check:** `pdftotext -v` fails.
- **Cause:** the CRA PDFs are read with pdftotext (poppler-utils).
- **Fix:** `sudo apt install poppler-utils` (Debian, Ubuntu), `brew install poppler` (macOS); or type the slips into `inputs/slips/slips.toml`.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/cra_slips.py` — `pdf_text`

### `tjs slip-audit --import-cra` hangs on a PDF, with no "pdftotext took over 60 s" error
- **Check:** `ps` shows the `pdftotext` it started still running minutes later.
- **Cause:** the 60-second limit covered only reading pdftotext's output: a pdftotext (or a program installed under that name) that closed its output and kept running was then waited for without a limit.
- **Fix:** upgrade: the one limit covers the reading and the process's exit, and the process is killed and reaped when it passes. On an older release, stop that pdftotext; type the slip into `inputs/slips/slips.toml` instead.
- **Fixed in:** `v0.27.1`
- **Code:** `src/taxjson/lib/cra_slips.py` — `_read_capped`, `pdf_text`

### `tjs slip-audit --import-cra`: one PDF shows as two slips ("merged.pdf #1", "merged.pdf #2"), or "skipped merged.pdf: box 24 twice in one T5 slip — the page cannot be read cleanly; not imported"
- **Check:** the PDF holds two slips (pages saved together, or files merged); `pdftotext -layout merged.pdf -` shows two "2025 T5 slip (original) from …" lines.
- **Cause:** each slip is read from its own slip line to the next one. Before, the first slip line was taken and the box rows of every later page were read into it: a T3 and a T5 became one slip (the T3's box 24 read as the T5's, boxes lost, placed in the wrong fund). A slip whose page repeats a box cannot be read cleanly and the whole file is refused.
- **Fix:** nothing for a merged PDF (each slip is a table, `source = "cra:merged.pdf #1"` …). For a refused file, download each slip on its own from My Account, or type it into `inputs/slips/slips.toml`.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/cra_slips.py` — `parse_slips`, `_sections`, `twice in one`

### `tjs slip-audit --import-cra`: "the same slip as t3.pdf — read once", "already in slips.toml ([[slip]] #1, the same boxes)", "replaced by the amended slip", or "slips.toml holds its amended slip"
- **Check:** `inputs/slips/slips.toml` has one table per slip; an amended slip's table says `status = "amended"`, and the original it replaced is commented out under "# Replaced by the amended slip …" (the file before is `slips.toml.bak`).
- **Cause:** each slip counts once. A second download (`t3 (1).pdf`), a file named with its folder, or a slip already in slips.toml (the same type, account, fund, broker account, currency and boxes) is not added again; an amended slip replaces the original of the same issuer, account, fund and broker account. Before, only the file name was compared and the status ignored: a re-download or an original plus its amended slip summed (a return of capital counted twice).
- **Fix:** nothing. When it says "which cannot be told" (two originals an amended slip may replace), import the originals with `--write` first, then the amended slip; or delete the original's table by hand. A cancelled slip is not imported: delete the table of the slip it cancels.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/cra_slips.py` — `drop_duplicates`, `plan_import`; `src/taxjson/lib/slip_audit.py` — `comment_out_tables`; `src/taxjson/bin/taxjson_run.py` — `_slip_audit_import_cra`

### `tjs slip-audit --import-cra`: "skipped t5.pdf: a French-language slip page", "box 27 (foreign currency) reads 'Zorkmids'", "a slip line taxjson cannot read", or "a USD slip and no USD rate for 2025 in the FX cache"
- **Check:** open the PDF: the page is CRA's French one, box 27 prints a currency name taxjson does not know, the slip line is not "YYYY T5 slip (original) from …", or the FX cache has no Bank of Canada rate for the year (`tjs run` offline).
- **Cause:** the importer reads CRA's English page only and never guesses: an unknown wording is refused, and a slip in another currency is placed only when its amounts can be set against the books' CAD. Box 27 may print `USD`, `US$` or `U.S. dollars`; "(Original)" is read as original.
- **Fix:** switch My Account to English and print the slip again; for a USD slip run `tjs run` online first (it fills the FX cache); else type the slip into `inputs/slips/slips.toml`.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/cra_slips.py` — `parse_slips`, `_currency`, `place`

### `tjs slip-audit --import-cra`: "its broker account cannot be told: the statement holds several IB accounts and no dividends report matches it"
- **Check:** the account's IB statement holds two IB accounts (one label exported together) and `inputs/slips/` has IB's dividends report for each, or none matches the slip's boxes 24 + 10, 18 and 15.
- **Cause:** the rows of one statement of two accounts cannot be told apart, so the slip is written with the account whose dividends report shows its figures, else the one account with no report; neither: it is not placed (a wrong account would make the other account's report "payments only").
- **Fix:** add the missing dividends report, or type the slip into `inputs/slips/slips.toml` with its `broker_account`.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/cra_slips.py` — `place`, `_report_matches`

### `tjs slip-audit`: "broker_key is the books' own hash of the broker account" or "broker_key 0a1b2c3d4e is no broker account in the books"
- **Check:** the `[[slip]]` table the message names has a `broker_key`; `ls work/.slip_key_salt`.
- **Cause:** `--import-cra` writes a key of the broker account salted with the project's own salt (`work/.slip_key_salt`), so the key in slips.toml cannot be turned back into an account number by trying every IB number. A key written by an earlier version is the books' unsalted hash (it still works, said); a key written with another salt (work/ deleted, a slips.toml copied from another project, a `tjs redact` copy) matches no account and its slip is compared with no books.
- **Fix:** delete the table and import the slip again (`tjs slip-audit --import-cra <file> --write`), or type `broker_account` instead.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/slip_audit.py` — `key_salt`, `broker_key`, `resolve_keys`; `src/taxjson/bin/taxjson_redact.py` — `_redact_broker_keys`

### `tjs slip-audit`: a T5 typed for the IB account and IB's dividends report counted twice ("Canadian dividends … slip" twice the books)
- **Check:** `inputs/slips/slips.toml` has a T5 with the IB account as `broker_account` and `inputs/slips/` has IB's dividends report for it.
- **Cause:** the typed slip and the report are the same T5. A typed slip for that broker account with a box the report covers is now compared, and the report keeps only its payments (as beside a CRA slip); a T5 typed for its interest only (box 13: the report has none) is compared beside the report. `--template` prints no table for a broker account the report covers.
- **Fix:** nothing.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/slip_audit.py` — `_audit_account`, `template`

### `tjs slip-audit`: "the same IB dividends report as U5***.2025.dividends.csv … read once" or "Error: … two different IB dividends reports of IB account U5*** for 2025 — keep the newer download only"
- **Check:** `inputs/slips/` holds two dividends reports of one IB account and year (`… (1).csv`).
- **Cause:** both used to be read: every payment counted twice. Two identical copies are read once; two that differ cannot both be right.
- **Fix:** delete one of them (the older download when they differ).
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/slip_audit.py` — `audit`, `report_identity`

### `tjs slip-audit`: "a second account (U5***, after U5***) — the report's payments do not say which account they are in; download one dividends report per account"
- **Check:** the dividends report's `Account` section has two rows (a report run for several accounts).
- **Cause:** the report's payment rows name no account, so a report of two accounts cannot be compared with either (before, the last account row was taken for every payment).
- **Fix:** download IB's dividends report once per account.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/ib_dividends.py` — `read_report`

### `tjs slip-audit`: "IB's report is in USD (the account's base currency) and the FX cache has no Bank of Canada USD rate for …"
- **Check:** the IB account's base currency is USD (the report's `Account` row); a `tjs run` online fills the FX cache.
- **Cause:** a Canadian slip is in CAD, so a USD-base account's report is converted payment by payment at the Bank of Canada rate of its pay date (tax-logic `CA-SLIP-02`; the Notes give the year's average-rate figure too). Before, it was compared in USD as if it were CAD.
- **Fix:** run `tjs run` online once (it fills the FX cache), then `tjs slip-audit`.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/slip_audit.py` — `ib_slips`, `_report_to_cad`, `_usd_base_note`

### `tjs run`: "Error: inputs/margin/U5***.2025.dividends.csv is IB's dividends report (the T5 / T3 income per payment), not an activity export — move it to inputs/slips/"
- **Check:** the file is in an account's folder, not in `inputs/slips/`.
- **Cause:** IB's dividends report is a slip source, not activity; the run stopped with "cannot detect broker" and the file's name (IB's download carries the account number) unmasked.
- **Fix:** move the file to `inputs/slips/`; `tjs slip-audit` reads it there.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `group_inputs_detailed`

### `tjs reconcile-slips inputs/slips/*.csv`: "Info: skipped U5***.2025.dividends.csv: IB's dividends report (T5/T3 income, read by `taxjson slip-audit`), not a T5008"
- **Check:** the skipped file is IBKR's dividends report.
- **Cause:** IB's dividends report lives in `inputs/slips/` beside the T5008 CSVs; it is T5/T3 income, which `reconcile-slips` does not read (before, it failed the reconciliation as an unreadable T5008).
- **Fix:** nothing: `tjs slip-audit` reads it. With only the report given, `reconcile-slips` stops with "no T5008 slip CSV to reconcile".
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_reconcile_slips`; `src/taxjson/lib/checklist.py` — `slip_files`

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

### `tjs checklist`: "[b] inputs-committed — not checked: this repository's own git config sets filter.crypt.clean, a command git would run"
- **Check:** `git config --show-scope --get-regexp '^(filter|diff)\.|^core\.attributesfile'` in the project lists the key with scope `local`. The same reason shows on `lock-committed`.
- **Cause:** `git status` runs the clean / process filter (and a diff textconv) that the repository's own `.git/config` names for the files `.gitattributes` assigns it to. A project folder received from someone else could run any command that way, so the checklist runs no git command in a repository whose own config sets one; filters in your global or system config (git-lfs) are fine.
- **Fix:** if you set that filter yourself and trust it, check `git status` by hand and mark the step: `tjs checklist --done inputs-committed`. Otherwise remove it (`git config --unset filter.crypt.clean`, and the `.gitattributes` line) and run the checklist again.
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/lib/checklist.py` — `_git_refusal`, `_GIT_COMMAND_KEYS`, `_git_status`, `d_inputs_committed`, `d_lock_committed`

## Stand-alone tools and hand-written JSON books

### `taxjson-gains book.json`: "impossible date='2025-02-30' (not a real calendar date written YYYY-MM-DD) — fix the input data", or a hand-written book whose gains change when a date is written `2025-2-01` instead of `2025-02-01`
- **Check:** the message names the row (its index, id and symbol) and the field: `date`, `date_settle`, `lot_date`, `record_date` or `ex_date`. On an older release, write the date `2025-02-01` and run again: if the gains (or a sale read as a short cover) change, it is this problem.
- **Cause:** the engines order rows by comparing their dates as text, so a date must be written year-month-day with two-digit month and day. A JSON book (one you wrote, or `taxjson-gains`, `taxjson-validate`, `taxjson-wash-radar` and the other stand-alone tools fed a file) with `2025-2-01` was accepted, and that row sorted after `2025-10-01`: FIFO and ACB took the wrong lot and a sale could open a short that a later purchase covered. The broker parsers and `.tt` files always wrote two digits.
- **Fix:** upgrade. A month or day written with one digit (`2025-2-1`) is now read as `2025-02-01`; anything else that is not a real date in `YYYY-MM-DD` form (`2025-02-30`, `2025/02/01`, `25-2-1`, a date with a time) stops with the message above. Correct the row and run again.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/core.py` — `coerce_transaction_row`, `canonical_date`, `DATE_FIELDS`

### `taxjson-gains book.json`: "Error: book.json: no "transactions" list (keys: Transactions)", or a hand-written book that gives empty gains and no inventory at exit 0
- **Check:** open the file: its top-level object has no key spelled exactly `transactions` (lower case); the message lists the keys it found.
- **Cause:** a transaction book is a JSON object with a `"transactions"` list, or a bare list of rows. An object without that key (`"Transactions"`, `"rows"`) was read as an empty book by `taxjson-gains` and the other tools that read a book file (`taxjson-wash-radar`, `taxjson-lint-crosslistings`, `taxjson-sum-income`, `taxjson-diff`, `taxjson-export --trades`, `taxjson-split-gains --base`): empty gains and inventory, no message, exit 0. The stdin reader already refused it.
- **Fix:** upgrade, then rename the key to `transactions` (or give the rows as a bare list). An explicit empty list (`{"transactions": []}`) is still an empty book.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/core.py` — `load_transactions`; `src/taxjson/lib/json_input.py` — `rows_or_exit`; `src/taxjson/bin/taxjson_sum_income.py` — `load_income_data`; `src/taxjson/bin/taxjson_diff.py` — `main`

### `taxjson-form-export gains.json`: "holds a non-finite number (NaN) — the file is damaged or hand-edited", or "refusing to write a non-finite number (NaN or infinity) at lines[0].gain"
- **Check:** search the named JSON file for `NaN`, `Infinity` or `-Infinity`: JSON has no such numbers, but Python's `json` module reads and writes them. On an older release the same file gave a successful export whose JSON held `NaN` (not valid JSON, and not a figure anyone can file).
- **Cause:** the report readers checked that a gain, cost or proceeds was a number, not that it was finite, so a damaged or hand-edited gains file (`work/<account>_gains.json`, a file passed to `taxjson-form-export`, `taxjson-sum-gains`, `taxjson-export`, `taxjson-t1135` and the other report tools) went through, and the JSON writers wrote the `NaN` back out.
- **Fix:** upgrade. Re-run `tjs run` to rebuild the work files; for a file you wrote, replace the value with a real number. The JSON writers of filing figures (`taxjson-gains`, `taxjson-form-export`, `taxjson-sum-gains`, `taxjson-sum-income`, `taxjson-carryover`, `taxjson-t1135`, `taxjson-split-gains` and the run's `<account>_report.json`) now refuse a NaN or an infinity, naming where it is, instead of writing it.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/json_input.py` — `read_json_doc`, `check_row_types`, `filing_json_text`, `dump_filing_json`, `NonFiniteOutputError`; `src/taxjson/lib/report_model.py` — `load_report_json`

### `taxjson-explain book.json`: "required field(s) net_amount missing on a BUYSELL row — the engine would book it at 0", or an explain trace with cost 0 for a purchase that `taxjson-gains` refuses
- **Check:** the message names the file and the row's index: that BUYSELL (or ASSIGN) row has no `net_amount` (or no `quantity`) key at all. On an older release, `taxjson-gains` refused the book while `taxjson-explain` traced the sale at cost 0, the whole sale as gain, at exit 0.
- **Cause:** a trade row without its amount gets the default 0. The loader marks such a row, but only `taxjson-gains` (and `taxjson-wash-radar`) checked the mark; `taxjson-explain`, `taxjson-audit`, `taxjson-carryover`, `taxjson-t1135` and any caller of `run_gains` computed with the 0, for the main book and the `--sheltered` / `--affiliated` books alike. A pass-through tool (`taxjson-sort`, `taxjson-merge2`, `taxjson-convert-currency`) also wrote the 0 out as if it were real.
- **Fix:** upgrade, then add the row's real `net_amount` (or `quantity`). The check now sits where every gains computation starts (the engines and the book preparation in front of them), for every book, from a file or stdin, and the pass-through tools keep the key missing. A real zero amount written as `"net_amount": 0` stays legal.
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/lib/core.py` — `require_computable_rows`, `coerce_transaction_row`, `carry_row_marks`, `TaxTransaction.to_dict`; `src/taxjson/lib/pipeline.py` — `prepare_books`

### `taxjson-gains book.json`: "required field date is empty — fix the input data", "required field symbol is empty on a BUYSELL row", or "unsupported action 'BUY'"
- **Check:** the message names the file and the row's index. Look at that row: `"date": ""`, `"symbol": ""` (or no symbol at all) on a row that moves a position or its cost, or an action other than `BUYSELL`, `ASSIGN`, `SPLIT`, `TRANSFER`, `ADJUST`, `OPENING_BALANCE`, `DISALLOW`, `DIVIDEND`, `DIVIDEND_IN_LIEU`, `INTEREST`, `TAX` or `FEE` (spelled in capitals). On an older release the book computed at exit 0: an undated sale sorted first and turned a later purchase into a short cover, a sale with no symbol opened a short in a security named "", and a row with another action was left out without a word.
- **Cause:** a missing or null date was refused when the row was read, but an empty one passed, and nothing checked that a trade names its security or that the action is one the engines book.
- **Fix:** upgrade, then correct the row. Rows that move a position or its cost (`BUYSELL`, `ASSIGN`, `SPLIT`, `TRANSFER`, `ADJUST`, `OPENING_BALANCE`, `DISALLOW`) need a symbol; a `FEE`, `INTEREST` or `TAX` row without one is fine. The check is the same one as for a missing amount above, in every gains computation.
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/lib/core.py` — `require_computable_rows`, `SYMBOL_REQUIRED_ACTIONS`; `src/taxjson/lib/brokerages/schema.py` — `KNOWN_ACTIONS`

### `taxjson-form-export gains.json`: ""transactions" row 1 (QZA.TO 2025-02-02): a disposition without gain — it would be left out of the totals and forms"
- **Check:** open the named file at that row (counted from 0): a disposition (any row that is not a `DIVIDEND` / `DIVIDEND_IN_LIEU` row and not flagged `tainted`) lacks its `qty` or `gain`, or holds `null` there, or has neither a `date` nor a `date_settle`. On an older release, the export (and `taxjson-sum-gains`, `taxjson-reconcile-slips`, close-year, check-filed, `taxjson-carryover`) left that disposition out of every total at exit 0 when another row of the same file was complete.
- **Cause:** whether a file was a gains file was decided for the whole document (any row with a gain), and the readers then skipped each row without a gain or units in silence (and a row without a date fell out of every year). The engines always write these, so such a row comes from a damaged or hand-edited file.
- **Fix:** upgrade. Re-run `tjs run` to rebuild a work file; for a file you wrote, complete the row. Dividend rows (`DIVIDEND`, `DIVIDEND_IN_LIEU`) need none of these, and an unknown-cost row flagged `tainted` needs only its date and units.
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/lib/json_input.py` — `require_gains_doc`, `check_gains_rows`, `gains_row_kind`; `src/taxjson/bin/taxjson_form_export.py` — `load_dispositions`; `src/taxjson/bin/taxjson_filed.py` — `aggregates_from_gains`; `src/taxjson/bin/taxjson_carryover.py` — `yearly_nets`

### `taxjson-form-export --csv s3.csv`: "cannot write --csv s3.csv: [Errno 17] File exists: 's3.csv.part'"
- **Check:** a file `s3.csv.part` sits beside the CSV, left by an earlier export that was killed or interrupted, or written by another export of the same file running at the same time.
- **Cause:** the CSV was written through the fixed temp name `<file>.part` and created it only if it did not exist, so a leftover one stopped every later export, and the failed write then deleted it (another export's unfinished data).
- **Fix:** upgrade: the CSV is now written through a new owner-only temp file of its own and renamed over the target, as the other taxjson writers do. On an older release, delete the leftover `.part` file once no export is running.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/bin/taxjson_form_export.py` — `write_csv`; `src/taxjson/lib/safe_write.py` — `atomic_open`
