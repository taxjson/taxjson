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
A bug an upgrade alone fixes stays listed here, its **Fix** starting with "upgrade", so search this
file whatever your release. `docs/upgrading.md` lists, release by release, what an upgrade asks of
you (figures that change on a re-run, a file or command that changed); its older "Problems an
upgrade fixes" list stops at v0.28.0.
**Code** names the file and the function or message to search for; `docs/architecture-map.md` maps
the rest. taxjson computes and shows its work; it gives no tax advice. The rules it applies are in
`tjs tax-logic` (with `--ids`).

## Installing and upgrading

### close-year: "Error: inputs/qt/ already holds files this project does not read"
- **Check:** `ls inputs/qt/` in the year folder (not the shared `../inputs/qt/`): the files there. The project reads the shared folder (`[settings] inputs_dir`), so nothing in its own `inputs/<account>/` is read (`taxjson run` warns about them).
- **Cause:** `close-year` freezes the inputs by copying each account's files from the shared folder into the project's own `inputs/<account>/`; a file already there (an export dropped in the wrong folder) would be mixed into the frozen copy, so it refuses and writes nothing.
- **Fix:** move those files away — into the shared `../inputs/<account>/` if they belong to the books (then `taxjson run` again), else out of the project — and run `taxjson close-year` again.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_freeze_inputs`, `already holds files this project`

### Installer: `TAXJSON_DRY_RUN=1` left a full clone (with `.git`) in `TAXJSON_DIR` although it printed "(dry run: nothing checked out or installed)"
- **Check:** `ls "$TAXJSON_DIR"` (default `~/.local/share/taxjson`) after a dry run into a folder that did not exist before: a clone of `TAXJSON_REPO` is there, and its `origin` is that repository.
- **Cause:** the installer's "2/4 Source" step cloned (or fetched) before the dry-run exit. Resolving a channel needs the tags and `origin/main:channels.json`, so something has to be fetched, but a missing install folder should not be created for it.
- **Fix:** upgrade: a dry run with no install resolves the channel in a temporary clone removed on exit and never creates `TAXJSON_DIR` or its parent; on an existing install it only fetches tags (the header, `docs/upgrading.md` and `docs/releasing.md` say so). On an older installer, delete the folder it left (`rm -rf "$TAXJSON_DIR"`) if you did not want an install there.
- **Fixed in:** `v0.30.4`
- **Code:** `install.sh` — `DRY_RUN`, `DRY_TMP`, `does not exist and is not created`

### Installer: "✗ channels.json names v0.30.0 for stable, but https://github.com/taxjson/taxjson.git has no such tag."
- **Check:** `git ls-remote --tags https://github.com/taxjson/taxjson.git` lists the releases the repository has; `cat ~/.config/taxjson/channel` shows the channel or release this install remembers; the message names the release and the channel it came from.
- **Cause:** the channel (or a release you pinned) names a release the repository does not have. v0.30.0 was withdrawn on 2026-10-12 and published again as v0.30.1 (CHANGELOG.md); an install pinned to it, or one that read `channels.json` in the minutes while it still named it, asks for a tag that is gone.
- **Fix:** re-run the installer on a channel (`--channel stable`), or pin a release the list above shows (`--channel v0.30.1`).
- **Fixed in:** —
- **Code:** `install.sh` — `has no such tag`

### Installer: "✗ A package pip downloaded does not match the sha256 that taxjson's lock (requirements/core.txt requirements/fx.txt) pins for it — refusing to install it."
- **Check:** above the message pip prints "THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE" with the package, the file it downloaded and the "Expected" and "Got" sha256; the whole log is in `~/.local/share/taxjson/venv/pip-install.log`. `pip config list` and `env | grep PIP_` show an index or proxy of your own.
- **Cause:** the installer installs every dependency from the release's hash-pinned locks (`requirements/*.txt`) and refuses a file whose sha256 the lock does not name, so a package uploaded to PyPI later (a hijacked release) never reaches an install. The usual innocent causes: a pip index, mirror or proxy that serves its own builds instead of PyPI's files (`PIP_INDEX_URL`, `pip.conf`), or a corrupted download cache. A file that really differs on pypi.org is a tampered package.
- **Fix:** never install the dependencies without the hashes. Clear the cache (`~/.local/share/taxjson/venv/bin/python -m pip cache purge`), let pip use pypi.org (unset `PIP_INDEX_URL` / `PIP_EXTRA_INDEX_URL`, or the index in `pip.conf`) and re-run the installer. If it still fails against pypi.org, report it privately (SECURITY.md) with the lines pip printed. Until then the install keeps its previous dependencies; `--channel vX.Y.Z` with your previous release goes back.
- **Fixed in:** `v0.28.4`
- **Code:** `install.sh` — `pip_locked`, `does not match the sha256`, `--require-hashes`; `scripts/lock_deps.py` — `compile_locks`

### Installer: "WARNING: extras [fx] NOT installed: no prebuilt wheel of pandas==… for Python 3.14.0 on Linux armv7l in taxjson's dependency lock"
- **Check:** `tjs --version` works but `~/.local/share/taxjson/venv/bin/python -c 'import yfinance'` fails; the Python and platform in the message are outside what the locks cover (Linux x86_64 and aarch64, macOS arm64 and x86_64, Python 3.10–3.13).
- **Cause:** the locks pin wheels only (nothing is built from source, which would fetch unpinned build tools), and the pinned version has no wheel for this Python or platform. The installer then installs the core package alone, its dependencies pinned too.
- **Fix:** run the installer with a supported Python first on `PATH` (e.g. `python3.12`; it takes the newest of 3.13 down to 3.10). Without `[fx]` taxjson still works with the Bank of Canada rates; Yahoo Finance rates (before 2007-05-01, other currencies, a non-CAD base) are missing.
- **Fixed in:** `v0.28.4`
- **Code:** `install.sh` — `pip_locked`, `NOT installed: no prebuilt wheel`; `scripts/lock_deps.py` — `coverage`, `PLATFORMS`, `PYTHONS`

## Setting up a project

### "Error: no taxjson.toml in …/taxes/2025 — not a taxjson project" (older releases: "… Run `taxjson init` first.")
- **Check:** `ls taxjson.toml` in the folder you ran from. Every project command says it; inside a project's sub-folder (its `inputs/`) the next line names the project: "The project is in ../..: `cd ../..`, or `taxjson -C ../.. sanity`".
- **Cause:** taxjson runs on the project in the current folder (or the one `-C DIR` names), and this folder has no `taxjson.toml`.
- **Fix:** `cd` into the project folder or pass `-C ~/taxes/2025`; for a new project, in the folder for your taxes (not a year folder): `mkdir -p ~/taxes && cd ~/taxes && tjs init --country canada --year 2025 && cd 2025`.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `load_config`, `_no_project_message`, `not a taxjson project`

### Outside a project: "Error: no gains files in …/work (run `taxjson run` first)." (sanity, wash-sales, list, find-missing-history, events, fees …), or fx-cash / stats / divs-sum: "Error: [settings] country is missing … in taxjson.toml" where there is no taxjson.toml
- **Check:** `ls taxjson.toml` in the folder you ran from (or the one `-C` names): there is none, and no year folders either.
- **Cause:** each command looked for its work/ files (or the country) before it looked for the project, so it sent you to `taxjson run`, which then failed with "no taxjson.toml", or blamed a key in a file that does not exist. Now every project command first says the folder is no project, and how to start one or which folder to `cd` to.
- **Fix:** `cd` into the project folder (the one holding taxjson.toml) or pass `-C DIR`; for a new project, `tjs init --country canada|usa` (docs/getting-started.md).
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_refuse_no_project`, `_NO_PROJECT_CMDS`, `_project_above`, `_no_project_message`

### Empty project: `tjs sum` says "Error: no gains files in work/" / "Run `taxjson run` first." right after `tjs run` said "no account had any input — nothing was computed"
- **Check:** every `inputs/<account>/` folder (`../inputs/<account>/` in the years layout) is empty, and `work/skipped_accounts.json` names every account. Since the fix `tjs sum`, `list`, `gains` and the other report commands say "Error: the last run had no input: inputs/margin is empty, so it made no books" (`../inputs/margin` in the years layout), naming each empty folder.
- **Cause:** the report commands did not read that the last run had no input at all, and sent you back to the run you had just done.
- **Fix:** put each broker's export in `inputs/<account>/` (`../inputs/<account>/` when every year shares one, docs/brokers.md), then `tjs run`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_refuse_no_input_books`, `_NO_INPUT_REPORT_CMDS`, `_accounts_skipped_for_no_inputs`

### Year folder: "Warning: no CSVs or .tt files in inputs/margin; skipping account 'margin'" and "put each broker's export in inputs/<account>/", right after "Warning: inputs/margin in this year's folder: not read"
- **Check:** `grep inputs_dir taxjson.toml` shows `inputs_dir = "../inputs"`: the project reads `../inputs/<account>/` (the folder holding the year folders), not the year folder's own `inputs/`.
- **Cause:** the skip line, the "nothing was computed" line and the `--details` "identified as" line named `inputs/<account>` whatever `inputs_dir` says, so they pointed at the folder the run had just said it does not read.
- **Fix:** move the exports into `../inputs/<account>/` and re-run. Since the fix those lines name `../inputs/`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `stage_account`, `no CSVs or .tt files in`, `_report_detection`, `nothing was computed`

### "Error: cannot write work/margin_questrade.json: No space left on device (disk full?) — free some space on the disk and run again" (older releases: "Error: [Errno 28] No space left on device" or "[Errno 27] File too large", printed twice, with no file and no "stopped at" line)
- **Check:** `df -h .` in the project folder (a full disk or quota), or `ulimit -f` (a file-size limit, "File too large"). The run's last line names the stage and the account: "Error: stopped at reading the broker files for account margin (…): cannot write work/…".
- **Cause:** a stage writes its output to a work/ file through a temporary file; the disk refused the write. Nothing is damaged: the previous work/ and reports/ files are kept until a complete new one replaces them.
- **Fix:** free some space on the disk (or raise the limit) and run `tjs run` again.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `run_to_file`, `_work_shown`, `_stage_text_shown`; `src/taxjson/lib/cli_diag.py` — `NO_SPACE_FIX`, `no_space_text`, `describe_input_error`; `src/taxjson/lib/safe_write.py` — `atomic_open`, `_name_full_disk`

### "Error: cannot write to work/: Permission denied" or "Error: cannot write rodir/x.csv: Permission denied" (older releases: "cannot write to work/" with no reason, "[Errno 13] Permission denied: '…/x.csv.l_97spz3.part'", or "permission denied: …/taxjson.toml.bak")
- **Check:** `ls -ld` on the folder the message names: no `w` for you.
- **Cause:** the folder the file goes in cannot be written (a copied or archived project, a folder made read-only). Nothing was written: the message names the file asked for, never the temporary or backup file taxjson stages beside it.
- **Fix:** run the `chmod u+w <folder>` the message gives, then the command again; for `taxjson-xlsx-to-csv`, or write the CSV elsewhere with another `-o`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/cli_diag.py` — `cannot_write`; `src/taxjson/bin/xlsx_to_csv.py` — `convert_xlsx_to_csv`, `or choose another -o`; `src/taxjson/bin/taxjson_run.py` — `cmd_format`, `makes it writable. Nothing was run.`

### "Error: cannot write taxjson.toml: the file is read-only" (also ticker.map, tobase.map, slips.toml; older releases replaced the file without a word)
- **Check:** `ls -l taxjson.toml` shows `-r--r--r--`: you (or a backup tool) made it read-only, often to keep a filed year as it was.
- **Cause:** the commands that rewrite a file you keep (`tjs format --write`, `format-map --write`, `migrate`, `init --force`, `ticker-map --suggest --write`, `slip-audit --import-cra … --write`, …) refuse one you cannot write. Nothing was written and no backup was taken.
- **Fix:** leave it if the year is filed and should stay as it is; otherwise `chmod u+w taxjson.toml` (the file the message names) and run the command again.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/safe_write.py` — `refuse_read_only`, `ReadOnlyFileError`, `READ_ONLY_FIX`, `write_user_file`; `src/taxjson/lib/migrate.py` — `refuse_read_only`; `src/taxjson/bin/taxjson_run.py` — `cmd_init`, `refuse_read_only`

### "Error: …/taxes/2026 is named like a tax year and is empty: `init` makes the year's folder itself, so this would build 2026/2026/ in it"
- **Check:** you ran `tjs init` inside an empty folder named like a year (an older installer and README said `mkdir -p ~/taxes/2026 && cd ~/taxes/2026 && taxjson init …`).
- **Cause:** `init` makes the year's folder (`YYYY/`) inside the folder it runs in, beside the shared `inputs/`; run in `~/taxes/2026` it built `~/taxes/2026/2026/` without a word.
- **Fix:** run it in the folder above, as the message says: `cd ~/taxes && tjs init --country canada && cd 2026`. A project already built nested works as it is; to tidy it, move `2026/2026/` up with its `inputs/` beside it. `tjs init --single` makes one folder for one year where you are.
- **Fixed in:** `v0.28.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_refuse_init_in_year_folder`, `cmd_init`

### "Error: `sum` works on one year's project, and this folder holds the year folders 2024, 2025 (with the exports they share)"
- **Check:** `ls` shows `inputs/` and year folders (`2024/`, `2025/`) but no `taxjson.toml`: the folder of exports every year shares (`tjs init`'s layout). `tjs years` lists the years.
- **Cause:** every command but `init`, `years`, `new-year`, `migrate`, `update-tobase-map`, `redact`, `tax-logic`, `help` and the release commands (`channels`, `deploy`, `promote`) works on one year's project, and this folder holds several; taxjson never guesses which.
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

### `tjs redact --check NAME.redacted.csv`: "Info: NAME.redacted.csv: already a redacted copy — skipped" and exit 0, whatever the copy holds; or `tjs -C inputs_redact redact --check`: "1 account id not replaced everywhere" on every file of a clean copy
- **Check:** `tjs --version`. On an older release `--check` names a `.redacted.` file as skipped without reading it, and a copy checked as a project warns about its own placeholders (`99900001`, `U9990001`).
- **Cause:** the check decided by the file name alone (a `.redacted.` name was skipped before the check ran), and a copy checked again had its own placeholders and pseudonyms taken for new ids, each replaced by itself and so "not replaced".
- **Fix:** upgrade, then check each copy again (`tjs redact --check FILE`, or `tjs -C DIR redact --check` for a copy laid out as a project). It reads a `NAME.redacted.EXT` file, or a file in a tree holding the `.taxjson-redacted` marker, takes the copy's own placeholders as redacted already, and exits 1 on anything else it finds. Writing still skips a `NAME.redacted.EXT` input.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_redact.py` — `in_redacted_copy`, `_is_placeholder_id`, `_is_pseudonym`, `redact_text`, `main`

### `tjs redact` in a year folder: a name in a comment of taxjson.toml or ticker.map is still in the copy, and the console listed no line of it to read ("Info: ticker.map: nothing to redact")
- **Check:** `tjs redact --check --details` in the year folder. On an older release taxjson.toml and ticker.map show counts only, with no "Read line N of the copy", and `grep -n '#' inputs_redact/taxjson.toml inputs_redact/ticker.map` shows the comment.
- **Cause:** the settings files' comments got the contact-detail patterns (phones, addresses, SINs, a name after a title or `transfer from`) but not the free-text review an export's lines get, so a bare name was kept without a word.
- **Fix:** upgrade: a comment of taxjson.toml, ticker.map or tobase.map that still holds name-like words or a 7+ digit number is listed under "Read line N of the copy" (the comments taxjson writes itself are not). Read those lines and edit the copy, or the comment, before sharing it.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_redact.py` — `redact_project_text`, `_generated_comments`, `_review`

### `tjs -C DIR redact --out REL`: "==> Done. Review REL/ before sharing it." and the copy is not in the project; or "a single-folder project of : inputs/", or the hint `taxjson -C inputs_redact/ run` stops with "Error: no such directory: inputs_redact/"
- **Check:** `ls -a REL` in the shell's folder: a relative `--out` folder is made there, with its `.taxjson-redacted` marker.
- **Cause:** a relative `--out DIR` is the shell's folder's (like every path argument, `-C` or not), but the closing line printed it as typed, so it read as a folder of the project (`Review inputs/` named the unredacted exports). The year's name was taken from `.` as given, and the run hint was relative to the project, not to the shell.
- **Fix:** upgrade: off the project's own folder (`-C DIR`, `--out DIR`) the closing line and the run hint give the copy's full path, and the year is named. On an older release, look for the copy in the shell's folder.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_redact.py` — `redact_tree`, `single-folder project of`

### `tjs new-year 2026`: the new taxjson.toml still lists last year's `holdings = [...]` for an account whose table is quoted or hyphenated (`[accounts."margin-main"]`, `[accounts.margin-main]`)
- **Check:** `grep -n holdings 2026/taxjson.toml` shows a line not commented out under such an account, although new-year said the accounts' holdings were commented out; or that account's other lines were commented out when `[estimate]` or `[instalments]` came just before it.
- **Cause:** new-year recognised only table names made of letters, digits, `_` and `.`: a quoted or hyphenated name was not read as a table, so the table before it stayed in effect.
- **Fix:** upgrade; for a year made by an older release, comment out (or delete) the account's `holdings` line in the new year's taxjson.toml and uncomment any account line commented by mistake (the new year's snapshots go in its holdings/).
- **Fixed in:** `v0.27.1`
- **Code:** `src/taxjson/lib/project_layout.py` — `new_year_text`, `toml_statements`

### "Error: taxjson.toml: cannot set settings.year in taxjson.toml safely (it would read differently): the table is written in a form this edit does not handle" from `tjs new-year`, `tjs align --write`, `tjs migrate`; or, on an older release, a taxjson.toml these commands left unreadable or wrong
- **Check:** look at how the table is written in taxjson.toml: an inline table (`settings = {country = "canada", year = 2024}`), dotted keys (`settings.year = 2024`), an array of tables (`[[distributions]]`), a year spelt `+2024` / `2_024`, or an account name with a dot (`[accounts."margin.one"]`). On an older release: after `new-year` the new folder still says `year = 2024` ("Error: [settings] year = 2024 but this folder is 2025") or has `prior_year_record` outside [settings]; after `migrate` (one shared tobase.map) "taxjson.toml is not valid TOML … Cannot declare ('settings',) twice"; after `align --write` a `[]` line, a distribution that stayed, an `[accounts.margin.one]` table, or ticker.map changed although the error said nothing was written.
- **Cause:** these commands edited taxjson.toml line by line and knew only `[table]` headers with one key per line: other spellings TOML allows were missed or written at the wrong level, and nothing checked the result.
- **Fix:** upgrade: every edit reads the keys as TOML does, writes inline tables, dotted keys and arrays of tables in place, and reads the result back — it must be the file with exactly that key changed, else the command stops and writes nothing (`align` checks every change before writing ticker.map or taxjson.toml; `migrate` before moving a tobase.map). With the new message, write the table out as `[settings]` with one key per line and run the command again. For a year made by an older release, fix `year`, `prior_year_record` and `[[distributions]]` by hand, or delete the new year folder and run `new-year` again.
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/lib/project_layout.py` — `set_key_text`, `_edit_text`, `new_year_text`, `flat_paths`; `src/taxjson/bin/taxjson_run.py` — `cmd_align`, `cmd_new_year`, `_migrate_to_years`; `src/taxjson/lib/tobase_map.py` — `apply_shared`

### `tjs new-year 2025` stopped part-way ("Error: cannot create 2025/: …", or "2025/holdings exists and is not a folder"), and running it again says "Error: 2025/taxjson.toml already exists"
- **Check:** `ls 2025/`: on an older release it holds a taxjson.toml but no holdings/ (or no ticker.map).
- **Cause:** new-year wrote taxjson.toml first and the other files after it, so a failure later left a configuration that blocks the retry.
- **Fix:** upgrade: the new year's files are made in a staging folder and moved into place only when complete (taxjson.toml last), so a failure leaves nothing to clean up — fix what the error names and run it again. For a folder left by an older release, delete `2025/taxjson.toml` (and anything else new-year wrote there) first.
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_new_year`, `_new_year_publish`

### "Error: 2025 has no year folder: start it first" from `tjs new-year 2026`; or, on an older release, a 2026 started from 2024 (`prior_year_record = "../2024/filed/2024.json"`, and `tjs handoff` there says "the record is for 2024; this project's year is 2026")
- **Check:** `tjs years` in the folder holding the years: is there a row for the year before the one you are starting? `grep prior_year_record 2026/taxjson.toml` names the lock the new year reads.
- **Cause:** new-year started the year from the newest earlier folder, whatever its year. With the year before missing, the new year read an older year's lock: its net capital losses and other carryforwards skipped whatever the missing year used, and the handoff checked the wrong pair of years.
- **Fix:** `tjs new-year 2025` first (it starts 2025 from 2024), then `tjs new-year 2026`. A 2026 made by an older release from 2024: move its own files aside, delete the folder, and make 2025 and then 2026 again (or set `prior_year_record = "../2025/filed/2025.json"` by hand once 2025 is closed).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_new_year`, `has no year folder: start it first`

### `tjs new-year 2025`: the commented-out `[estimate]` / `[instalments]` "kept for reference" lists each key twice (`# other_income = 90000`, then `# other_income = 0`), and uncommenting the table gives "Cannot overwrite a value" / a duplicate key
- **Check:** `grep -n "other_income\|prior_year_net_tax\|basis" 2025/taxjson.toml` shows two commented lines per key under the "2024's figures, kept for reference by `taxjson new-year`" note.
- **Cause:** `taxjson format` (which new-year's copy goes through) kept the commented-out line of last year's value and wrote the template's own example line for the same key under it, its `=` out of line with the others.
- **Fix:** upgrade: in a commented-out table, a commented-out key of yours with no example line beside it takes the example's place, aligned, so a new year lists each key once. For a year made by an older release, delete the template's example line of each pair (the second; keep last year's value, or put this year's in) before uncommenting the table, then `tjs format --write`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/config_template.py` — `own_example`, `own_examples`, `examples`; `src/taxjson/lib/project_layout.py` — `new_year_text`; `src/taxjson/bin/taxjson_run.py` — `_templated`

### `tjs align --from 2024` or `tjs years --diff 2024 2025` says "the same ticker.map rules and settings" although 2024/taxjson.toml does not read, or the accounts are in another order
- **Check:** `tjs -C 2024 run` (does its taxjson.toml read?); compare the order of the `[accounts.NAME]` tables in the two files.
- **Cause:** a taxjson.toml (or ticker.map) that could not be read was compared as an empty one, and the accounts' order was not compared; the tables are processed in the order written, so trades at the same moment in two accounts can book differently.
- **Fix:** upgrade: a file that cannot be read stops `align` / `years --diff` with an error naming it (`tjs years` lists it as the year's problem), and a different order is listed as `accounts (order)`. `align --write` never reorders the tables: move them by hand if this year should follow the other's order.
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/lib/project_layout.py` — `compare`, `account_order`, `map_rules`, `ACCOUNT_ORDER_KEY`; `src/taxjson/bin/taxjson_run.py` — `cmd_align`, `cmd_years`

### After `tjs migrate --to-years`: "ticker.map is a symlink to maps/current.map, which does not exist (or loops)", or `tjs sanity` finds none of the positions snapshots in the folder `holdings_dir` names
- **Check:** `ls -l 2024/ticker.map` (a link by a relative path, moved unchanged); `grep holdings_dir 2024/taxjson.toml` names a folder that is still at the top.
- **Cause:** migrate moved a relative ticker.map link into the year folder without changing its target, and moved only `holdings/`, not a holdings folder named otherwise.
- **Fix:** upgrade: a moved link by a relative path is made again from the year folder, naming the same file; a `holdings_dir` folder beside taxjson.toml moves with it (one further away is named from the year folder, `"../data/snaps"`). For a project migrated by an older release, `ln -sfn ../maps/current.map 2024/ticker.map`, and move the snapshots folder into 2024/ (or set `holdings_dir = "../snapshots"`).
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_migrate_to_years`

### `tjs format --check` right after `tjs migrate --to-years`: "Warning: taxjson.toml is not formatted", the diff moving `exports_dir` (and `inputs_dir`, `tobase_map`, `requires_taxjson`) from the end of `[settings]` into their places
- **Check:** `tjs -C 2024 format` shows the keys migrate set going from the last lines of `[settings]` to their commented places in the template; `tjs -C 2024 format --check` exits 1.
- **Cause:** `migrate --to-years` appended the keys it sets at the end of `[settings]`, unaligned, with the template's commented line for each still above — a file in the template layout left it.
- **Fix:** upgrade: a `taxjson.toml` in the template layout stays in it (the keys go in their places, as `new-year` writes them); one not in the layout is changed only where migrate must. For a project migrated by an older release, `tjs -C 2024 format --write` (nothing it changes is read differently).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_migrate_to_years`, `_templated`, `_was_templated`

### "Info: ../inputs/: rrsp2 — not an account of 2024 (no [accounts.NAME] here): not read"
- **Check:** `tjs years`: another year's `taxjson.toml` has `[accounts.rrsp2]` (an account split, opened or closed in another year).
- **Cause:** every year reads the shared `inputs/`, but a year's books hold only the accounts its own `taxjson.toml` declares.
- **Fix:** nothing when the account is not this year's. When it is, add the `[accounts.rrsp2]` table (`tjs align --from 2025` brings it from the year that has it) and `tjs run`.
- **Fixed in:** `v0.26.0`
- **Code:** `src/taxjson/lib/project_layout.py` — `unconfigured_inputs`; `src/taxjson/bin/taxjson_run.py` — `not an account of`

### "Warning: these books are not the clean result of the current inputs (1 problem; the filed year's inputs changed): `taxjson checklist`"
- **Check:** the same command with `--details` (`tjs sum --details`) lists the problems: "inputs changed since the last full run (added: inputs/margin/late.tt)" and "2024 is filed (filed/2024.json) and its inputs changed since the last run". `tjs years` shows the filed year with "inputs changed since"; the file is a download saved in the shared `inputs/` while working on a later year, or (in a year closed with its inputs frozen) a file put in the year's own `inputs/<account>/`.
- **Cause:** a filed year closed before v0.31.0 still reads the shared exports, so a new or corrected file there can change its figures; a year closed since reads only its own frozen copy, which changes only when a file is put in it. Its books in `work/` were built before the change.
- **Fix:** `tjs run` in the filed year's folder: it recomputes the year and warns `filed 2024 DRIFTED vs 2024.json` when the filed figures moved (then amend the return, or keep the books consistent with it), or says `filed 2024: OK`. A year closed with frozen inputs never sees a file saved in the shared `inputs/`: next year's `tjs handoff` shows it (see "A filed year and later downloads" in getting-started.md).
- **Fixed in:** `v0.26.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_filed_year_stale_hint`, `_check_filed_years`; `src/taxjson/lib/checklist.py` — `d_filed_lock`, `inputs_changed`

### A corrected export, a late election or an `OPENING` line saved in the shared `inputs/` does not change a filed year: its `tjs run`, `tjs check-filed` and `tjs years` row stay OK
- **Check:** `grep -n "## frozen by" 2024/taxjson.toml` (the year was closed with its inputs frozen); in the next year, `tjs handoff` lists the difference (an opening quantity, or a cost at Dec 31, that differs from the filed year-end books) and `tjs option-boundary` an option open across Dec 31.
- **Cause:** by design since v0.31.0: close-year copies the exports the filed books came from into the year's own `inputs/` (and `tobase.map`), and the year reads only that copy, so the shared folder can move on with the next year. getting-started.md and filing.md said a later download would make the filed year warn `DRIFTED`; they now say what the freeze does, and a file written into the shared folder (`tjs opening`, `tjs elect … --set` …) names the frozen years it does not reach.
- **Fix:** to carry the correction into the filed year, put the corrected file in its `inputs/<account>/` (or run `tjs elect` in its folder), then `tjs run` there: it names the frozen input that changed and warns `filed 2024 DRIFTED` when the filed figures moved. Amend the return, then `tjs close-year --force`; or keep the books as filed.
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_frozen_years_apart`, `_shared_inputs_note`, `_note_shared_decision`, `_freeze_inputs`

### A corrected sale saved in the shared `inputs/` (its proceeds, or the cost of a lot the filed year sold) shows nowhere: the filed year's `tjs check-filed` says OK and next year's `tjs handoff` says "Everything the closed year carried forward is here, once."
- **Check:** `tjs -C 2024 check-filed` (the filed year's folder): "Info: filed 2024: 1 shared export(s) changed or lost a row of 2024 or earlier since the year was closed: ../inputs/margin/trades.tt".
- **Cause:** the filed year reads the copy close-year froze, and next year's handoff compares only the positions and cost at Dec 31, so a sale's own figures that changed in the shared export reached no check. Rows the shared file only adds (later years') are no change; a changed or removed row of the filed year (or earlier) is.
- **Fix:** compare the shared file with the copy in the filed year's `inputs/<account>/`. To amend, copy the corrected file in, `tjs run` there (it names the frozen input that changed and warns `DRIFTED` when the filed figures moved), file the T1-ADJ, then `tjs close-year --force`; or keep the books as filed.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_shared_exports_moved`, `_line_year`, `_check_filed_years`

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

### "Warning: inputs/margin/missing_history.tt:3 opens 10 QZQ.US / margin: the position goes short again on 2025-11-03 (40 units) after those units are used up" (or "… on 2024-03-01, but the position is already short on 2024-02-01 (60 units)")
- **Check:** `tjs find-missing-history` shows "the run opens the 10 units its line states" under the position.
- **Cause:** a `.tt` `OPENING ... cost=unknown` line opens its quantity on its date, exactly: the account sells more than that and its purchases in the files, or sells before the line's date.
- **Fix:** raise the quantity to the units held before the data (or add the missing purchase); date the line before the first sale it covers — the day before the account's first row is always early enough. A real short sale needs nothing.
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/lib/missing_history.py` — `short_again_message`, `_apply_fixed_openings`

### "Warning: inputs/margin/missing_history.tt:8 lists SAMPA.TO / margin, but its rows never go short any more" (the purchase was added; `tjs sum` still says "1 sale with an unknown cost NOT in these totals")
- **Check:** `tjs find-missing-history` lists the position under `STALE in missing_history.tt`; `tjs list` holds the units the line opens after the sale (the broker holds none); `tjs checklist` has the missing-history step at attention ("entry still opens its units").
- **Cause:** a `.tt` `OPENING ... cost=unknown` line opens its quantity as written, never sized from the rows (`CA-ACB-11` / `US-BASIS-04`). Once the purchase is in the books (an older export, a `.tt` BUYSELL line), the position never goes short without the line, yet the line still adds its units at an unknown cost on top of the purchase: the sale draws on the unknown-cost pool, stays out of the totals, and the books keep units the broker does not hold. Before the fix the run said nothing, the checklist step stayed done, and `find-missing-history` called the line one that "does nothing".
- **Fix:** delete the line from `missing_history.tt` and `tjs run`: the sale goes back into the totals with the purchase's cost. Keep it only if those units really were held besides the purchases in your files (the books then match the broker's holdings: `tjs sanity`).
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/lib/missing_history.py` — `synthesize_openings`, `complete_entry_message`, `stale_missing_history_entries`; `src/taxjson/lib/checklist.py` — `d_missing_history`, `STALE in `; `src/taxjson/bin/taxjson_missing_history.py` — `PURCHASE NOW IN THE BOOKS`

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

### `tjs checklist`: "! 20 inputs-committed: ../inputs/ is not in this project's git repository — keep the... — git status"
- **Check:** `tjs checklist --all` shows the step as "[!] 20. inputs-committed" with the whole text; `git -C 2025 rev-parse --show-toplevel` and `git -C inputs rev-parse --show-toplevel` name different folders (or the second fails).
- **Cause:** the year folder is its own repository, so the shared `inputs/` beside it is committed nowhere the checklist can see.
- **Fix:** one repository for the whole folder: `git init` in the folder holding the year folders (move a year folder's history in with `git subtree`, or start fresh), then commit `inputs/` and the year folders.
- **Fixed in:** `v0.26.0`
- **Code:** `src/taxjson/lib/checklist.py` — `d_inputs_committed`, `project's git repository`

### `tjs checklist`: "! 20 inputs-committed: not a git repository — `git init ..` (the folder above), then commit" (earlier releases: "not a git repository — git status", and `git status` fails)
- **Check:** `git status` in the year folder says "fatal: not a git repository"; the step says `git init ..` when the folder above holds the year folders (one folder of exports for every year), `git init` for a single-folder project.
- **Cause:** nothing records the inputs the filed books were built from: the project is in no git repository. The step named `git status`, which fails there, and nothing named `git init`.
- **Fix:** `git init` in the folder the step names (from a year folder, `git init ..`: one repository for the year folders and the shared `inputs/`; `tjs init`'s `.gitignore` there keeps `work/` and `reports/` out), then `git add -A` and commit. Not keeping the project in git: `tjs checklist --done inputs-committed --force`, and `tjs close-year --yes`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/checklist.py` — `d_inputs_committed`, `multi_root`, `(the folder above)`, `_do`

### "Error: this project needs taxjson 0.27.1 or newer (installed: 0.27.0) — upgrade: `tjs deploy` or re-run the installer" (or "2026/ needs taxjson …")
- **Check:** `tjs --version`, and `grep requires_taxjson taxjson.toml` (in the folder holding the year folders, each year's): the project records the oldest taxjson its layout runs on. Every command stops here, exit 2, before reading anything else. "Error: [settings] requires_taxjson must be ">=X.Y.Z", …" means the value itself is not that form.
- **Cause:** the project was made or converted by a newer taxjson (`init`, `new-year`, `migrate`, `update-tobase-map --write`), and its layout needs that release: an older one ignored settings it did not know — a project with one shared tobase.map (`tobase_map = "../tobase.map"`, v0.27.1) run with v0.27.0 read no tobase.map at all and failed on an unrelated line, and could have computed other figures without a word.
- **Fix:** upgrade: `tjs deploy` on a development checkout, otherwise re-run the installer. Do not lower the value by hand: the layout really needs that release. A malformed value: write it as `requires_taxjson = ">=0.27.1"` (`tjs migrate` writes the right one).
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/lib/requires.py` — `FEATURES`, `problem`, `raise_requirement`; `src/taxjson/bin/taxjson_run.py` — `_refuse_newer_projects`, `_refuse_newer_config`

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
- **Cause:** a misspelled key or table is not read, so its default applies (an `[estimates]` table means 0 other income in `tjs estimate`). An unknown `[settings]` key is said by every command (from v0.28.2; before, by `tjs run` only), once: it may also be a setting of a newer taxjson than the one installed (`tjs --version`).
- **Fix:** correct the spelling as suggested. `tjs format` shows every key the country's projects read, with its description and default. A key a newer release added: upgrade.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `validate_config`, `_config_table_warnings`, `_unknown_setting_warnings`, `unknown top-level`

### "Error: [settings] ric_january_dividends is United States-only (…); this project is country = "canada" — remove it"
- **Check:** every command stops at "Checking the project" (exit 1). The reverse happens for a Canada-only key such as `province`, `option_premium_timing` or `corporate_distributions` in a US project, and for a `base_currency` that is not the country's.
- **Cause:** each setting and table belongs to one country (or both); a key of the other country would be silently ignored or applied under the wrong rules, so it is refused.
- **Fix:** delete the line, or fix `country` if that is what is wrong. `tjs format` shows only the keys this country reads.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/country.py` — `_owner_problem`, `config_country_problems`, `SETTING_COUNTRY`

### "Warning: taxjson.toml: inputs/joint/ contains data but has no [accounts.joint] section"
- **Check:** `tjs run --details` adds the line "It will NOT be processed"; no `==> joint` step appears in the run.
- **Cause:** each folder under `inputs/` is read only when `taxjson.toml` has an `[accounts.<folder name>]` table for it. The folder name is the account name.
- **Fix:** add `[accounts.joint]` with `type = "taxable"` (or `"sheltered"`; `crypto = true` for Coinbase or Kraken files), or move the files into an existing account's folder. Then `tjs run`.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `validate_config`, `contains data but has`

### "Error: [accounts.Margin] and [accounts.margin] differ only by letter case — an account's name is also its inputs/ folder and its holdings file's name, which letter case does not tell apart"
- **Check:** taxjson.toml has two `[accounts.NAME]` tables whose names are the same apart from capitals.
- **Cause:** an account's name is also its `inputs/` folder, its `work/` files and the name a `holdings/` snapshot is matched by, and those matches ignore letter case (on Windows and macOS the two folders are one). Such names were accepted, and both accounts' snapshots went to one of them while the other was not checked.
- **Fix:** rename one account (its table and its `inputs/` folder), for example `margin` and `margin-2`.
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/lib/config_check.py` — `account_pair_problems`, `differ only by letter case`

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

### "Error: [accounts.crypto] is a crypto account but [settings] has no local_timezone" though taxjson.toml sets the zone under a misspelled key (`local_timezoen`)
- **Check:** `grep -n timezo taxjson.toml` shows the key spelled otherwise; `tjs checklist --run` prints "Warning: taxjson.toml: unknown [settings] key 'local_timezoen' is ignored (did you mean 'local_timezone'?)". Since the fix the run's error and the checklist's configure step name it: "has no local_timezone (it has 'local_timezoen': is that local_timezone misspelled?)".
- **Cause:** the run checks the zone before it prints the unknown-key warnings, so the line naming the typo was never shown. The checklist's configure step also offered `tjs migrate` and `tjs align --from <last year>` in a first project, where both fail ("nothing to migrate", "no project in …/2023").
- **Fix:** rename the key `local_timezone`. Since the fix the configure step offers `tjs migrate` only beside old map files, and `tjs align --from` only with another year's project beside this one (from the latest earlier year).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/brokerages/_crypto_common.py` — `timezone_typo`, `timezone_typo_note`; `src/taxjson/bin/taxjson_run.py` — `_normalize_settings`; `src/taxjson/lib/checklist.py` — `s_configure`, `_project_cmds`

### "Error: 2 ticker.map problem(s)"
- **Check:** each problem follows as `- ticker.map:<line>: …`, e.g. "CRYPTO needs `CRYPTO SYMBOL YAHOO_ID`" or "line has no ticker.map keyword (GLOBAL/TOBASE/…)". `tjs run` stops (exit 1).
- **Cause:** a line that cannot be parsed, a line without its keyword (an old `yf_ticker.map` or `crypto_ticker.map` line pasted in), or contradictory rules (a rename cycle, two targets for one symbol). A dropped rule would change ACB pools and gains, so the whole map is refused.
- **Fix:** fix the named line (its keyword's fields separated by spaces, as the ticker.map header and [settings.md](settings.md) give them; notes after `#`), e.g. `CRYPTO QZQC QZQC9999`, or delete it.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `ticker.map problem(s)`; `src/taxjson/lib/ticker_map.py` — `read_side_rules`, `parse_side_line`, `line has no ticker.map keyword`; `src/taxjson/bin/taxjson_ticker_map.py` — `map_file_problems`

### `tjs t1135`: "Error: ticker.map:54: 'ZZC CA' has no ticker.map keyword — a T1135 override is a `T1135 SYMBOL COUNTRY` line" (older releases: "… (`taxjson migrate` converts an old t1135.map)", and the checklist's t1135 step "(blocked): /…/ticker.map … — tjs migrate")
- **Check:** the named line of `ticker.map` is a symbol and a country with no keyword in front (`ZZC CA`). An older `tjs t1135 --details` told you to write `SYMBOL CA`, and `tjs migrate` then said "nothing to migrate".
- **Cause:** a T1135 domicile override is a `T1135 SYMBOL COUNTRY` line; a line without the keyword might be an old t1135.map line pasted in, and a dropped override can flip the filing verdict, so T1135 stops on it. The notes left the keyword out, and the refusal named `taxjson migrate` for every such line although there was no t1135.map to convert.
- **Fix:** put `T1135` in front of the line (`T1135 ZZC CA`), or fix or delete it if it is no override; then `tjs t1135`. `tjs migrate` is named only when an old `t1135.map` is in the project. The checklist's blocked line now shows the problem and names `tjs t1135`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_t1135.py` — `load_overrides`, `_overrides_problem`, `has no ticker.map keyword`; `src/taxjson/lib/checklist.py` — `d_t1135`

### "Warning: similar fund in a loss's window: QZSP.US sold at a loss, QZVO.US bought within 30 days"
- **Check:** `tjs run --details` shows the sale, the purchases (account and date, `registered` for a plan) and the ticker.map `SIMILAR` line that pairs them; `tjs checklist --all` lists the pairs under wash-reviewed; `tjs buy-check QZVO.US` adds the same caveat.
- **Cause:** your ticker.map declares the two funds look-alikes (`SIMILAR QZSP.US QZVO.US ...`) and one was bought inside the other's loss window (in Canada and still held at day 30). taxjson keeps them two securities, so the loss is allowed and nothing is denied: the superficial-loss rule reaches identical property (IT-387R2), §1091 substantially identical securities, and whether another issuer's fund is one is your call, not the books' (tax-logic CA-SL-20 / US-WASH-26).
- **Fix:** decide the pair by hand (the warning is said on every run while the line stands; mark wash-reviewed done once you have). If the two are one security after all (one issuer's two listings), the line is `TOBASE`, not `SIMILAR`; if you no longer want the pair watched, delete the line. A `SIMILAR` line never changes a figure.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/similar.py` — `analyze`, `message`; `src/taxjson/bin/taxjson_run.py` — `_say_similar_losses`

### `tjs buy-check QZVO` (the bare ticker) answers plain "SAFE" for a fund you never held, while `tjs buy-check QZVO.TO` gives "SAFE*" and the SIMILAR caveat
- **Check:** ticker.map has a `SIMILAR` line naming `QZVO.TO`, a member of it was sold at a loss in the past 30 days, and nothing in the books ever traded `QZVO.TO`.
- **Cause:** a bare ticker resolved only to listings in the books (or joined to them by ticker.map), so a look-alike never held stayed unresolved and its caveat was dropped. A bare ticker now also names the listing a `SIMILAR` line names, and buy-check and sell-check take the caveats of every listing it names.
- **Fix:** upgrade; until then, query the listing with its suffix (`tjs buy-check QZVO.TO`).
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_bare_listings`, `_class_matches`, `_similar_lines`

### `tjs sell-check QZSP` gives no SIMILAR caveat although a look-alike fund was bought in the past 30 days in a TFSA, RRSP or IRA (the run warns "similar fund in a loss's window"); `tjs wash-radar` shows the look-alike CLEAR
- **Check:** `tjs wash-radar --all` shows the look-alike's row as "CLEAR: Held only in SHELTERED account(s) ('tfsa' bought it …)" (US: "Held only in IRA(s)").
- **Cause:** sell-check's caveat read only the radar categories of a taxable buy (LOCKED, EXITABLE, CAUTION). A fund held only in a registered account or IRA is filed CLEAR whatever its purchase date, so its in-window buy never matched, though the pair is watched for a buy "in any of your accounts" (tax-logic CA-SL-20 / US-WASH-26). The radar row now carries that buy (`sheltered_buy`), and sell-check adds the caveat, SAFE*.
- **Fix:** upgrade.
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/bin/taxjson_wash_radar.py` — `sheltered_buy`; `src/taxjson/bin/taxjson_watch.py` — `flatten_radar`; `src/taxjson/bin/taxjson_run.py` — `_similar_lines`; `src/taxjson/lib/similar.py` — `caveat`

### "Error: 1 ticker.map problem(s)" with "ticker.map:4: SIMILAR symbol 'QZSP' must be a share listing with its suffix (ZZSP.US, ZZVF.TO): never an option, a future or a bare ticker"
- **Check:** the named line of ticker.map.
- **Cause:** a `SIMILAR` line needs two or more share listings written as the books spell them, `ROOT.SFX` (a suffix `taxjson/data/markets.toml` knows): a bare ticker names no listing, and an option or a future is never a look-alike fund.
- **Fix:** write each member with its suffix (`SIMILAR QZSP.US QZVO.US QZVF.TO`), or delete the line. `tjs run` refuses the map until the line is fixed, as for any malformed line.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/ticker_map.py` — `parse_side_line`, `_share_listing_spelling`

### "Info: tobase.map: ticker.map decides 2 of its pair(s) otherwise (ticker.map wins)"
- **Check:** `tjs run --details` lists the pairs under it, `- TOBASE QZAB.US QZA.TO not applied (ticker.map: TOBASE QZAB.US QZZ.TO)`; `tjs update-tobase-map` lists the same pairs.
- **Cause:** a Canadian project reads tobase.map (the interlisted master's pairs) with ticker.map, and ticker.map wins: a `TOBASE`, `JOURNAL`, `GLOBAL`, `DELETE` or dated `RENAME` of either listing, a `TOBASE` that books another listing under the one the pair would move, or a `DISTINCT` pair keeps the master's line from applying.
- **Fix:** nothing, if your line is right (it is what the books use). If the master's pair is right, delete your ticker.map line and re-run. A pair your map pools the same way is never listed.
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/lib/tobase_map.py` — `compute_overlay`; `src/taxjson/bin/taxjson_run.py` — `_say_tobase_map`

### "Warning: QZG.US has 2 row date(s) after 2025-06-30, when its interlisting ended (tobase.map:41: `TOBASE QZG.US QZG.TO`)"
- **Check:** the dates the warning lists after its last `:` (on a narrow terminal, with `tjs run --details`); the security name on those rows (`tjs trades`, `tjs events`). The tobase.map line ends `until=2025-06-30`.
- **Cause:** the master records that the pair stopped trading as one security on that date (an acquisition, a delisting). A later row in that ticker may be another company's: US tickers are reused, and the line would pool it with the Canadian listing.
- **Fix:** if those rows are another security, add `DISTINCT QZG.US QZG.TO` to ticker.map (it keeps the two apart at every date; book the old security's earlier rows under a symbol of their own with a dated `.tt` `RENAME` line if the year holds both). If they are the same security (a late corporate-action row), nothing.
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/lib/tobase_map.py` — `until_findings`, `until_message`; `src/taxjson/bin/taxjson_run.py` — `_say_tobase_until`

### "Warning: QZG.US has 3 row date(s) in the books and the ticker is reused: today it names QZ OTHER CO (tobase.map:41: `TOBASE QZG.US QZG.TO`)"
- **Check:** the dates the warning lists (on a narrow terminal, with `tjs run --details`) and the security name on each row (`tjs trades`, `tjs events`); the tobase.map line carries `reused_by="..."`.
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

### "Error: 1 tobase.map problem(s)" (or "1 ticker.map and tobase.map problem(s)"; on v0.35.0 and older "1 ticker.map problem(s)") with "tobase.map:12: tobase.map holds `TOBASE FROM TO` and `DISTINCT A B` lines only"
- **Check:** the named tobase.map line; `tjs update-tobase-map` lists marked lines you edited.
- **Cause:** tobase.map is read with ticker.map, and a line it cannot use would drop a pair silently, so the run stops as for a ticker.map problem. The headline names the file(s) the problems are in (it said "ticker.map" for a tobase.map line).
- **Fix:** move your own rule to ticker.map (any keyword lives there) and delete it from tobase.map, or run `tjs update-tobase-map --write` to lay the file out again (your own unmarked `TOBASE` / `DISTINCT` lines are kept).
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/lib/tobase_map.py` — `parse_tobase`; `src/taxjson/bin/taxjson_ticker_map.py` — `_parse_map_file`; `src/taxjson/bin/taxjson_run.py` — `_tm_files`

### `tjs run` or `tjs checklist --run`: one ticker.map line warned again and again ("Warning: ticker.map:54: GLOBAL renames a symbol to itself (no effect): … — skipping" twice, then "Info: 20 more like …"; or each "Warning: ticker.map problem: …" twice above the checklist and "Info: 14 more like …" after it)
- **Check:** `tjs run --details | grep -c 'to itself'` counts one line per pass that read the map on an older release.
- **Cause:** every pass of the run (and every check of the checklist) that reads the map in-process said each of its messages again; the fold then showed two copies of each and a count line.
- **Fix:** upgrade: each map message is shown once per run (a no-op line under `==> Checking the project`) and once per checklist; each stage's captured .diag (and the .sum DIAGNOSTICS) still has its line. Fix or delete the line it names.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/ticker_map.py` — `say_map_message`, `MAP_MESSAGES_SAID`; `src/taxjson/bin/taxjson_ticker_map.py` — `load_map_file`; `src/taxjson/bin/taxjson_run.py` — `_cmd_run`

### "Error: project file(s) that exist but cannot be read — fix or remove each; nothing was run: ticker.map is a directory, not a file", printed twice around "==> Checking the project"
- **Check:** `ls -ld ticker.map` shows a folder (or a link that leads nowhere) where the file should be.
- **Cause:** the file must be a readable file; a folder, a dangling link or one that loops is refused before anything runs. The help page's country lookup read the project first and printed the same refusal before the command's own.
- **Fix:** remove the folder (or the link) and put the map back (`tjs init` writes an empty one in a new project), then `tjs run`. An upgrade prints the refusal once.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_refuse_unreadable_project_inputs`, `_help_country`

### `tjs checklist`: "! 13 tobase-map: tobase.map is from the master of 2026-01-01; this... — tjs update-tobase-map"
- **Check:** `tjs checklist --all` shows the whole line: "tobase.map is from the master of 2026-01-01; this taxjson has the one of 2026-10-01". `tjs update-tobase-map` (a dry run) lists what an update adds, ends and retracts, and the pairs that name a symbol of your books.
- **Cause:** an upgrade installed a newer interlisted master; the project's tobase.map was made from an older one (its `# master-generated` line).
- **Fix:** `tjs update-tobase-map --write` (the previous file is kept as `tobase.map.bak`), then `tjs run`. In a project with several year folders the years read one shared tobase.map (since v0.27.1): run it once, in any year folder or in the folder holding them, then `tjs run` in each year it names (a filed year closed with its inputs frozen reads its own copy and is not named). Years that still keep a copy each: `tjs migrate` there first.
- **Fixed in:** `v0.27.0`
- **Code:** `src/taxjson/lib/checklist.py` — `s_tobase_map`; `src/taxjson/bin/taxjson_run.py` — `cmd_update_tobase_map`

### `tjs update-tobase-map`: "Retracted: … `DISTINCT QZD.US QZD.TO`: not needed: look-alike listings are never joined" (or `tjs checklist`: "! 13 tobase-map: tobase.map holds 1 DISTINCT line(s) an earlier version... — tjs update-tobase-map"; `--all` shows the whole line)
- **Check:** `grep -n '^DISTINCT' tobase.map` (in a multi-year project, the `tobase.map` beside the year folders): lines marked `# master:<FIGI>` under "Depositary receipts the books hold".
- **Cause:** v0.27.0 wrote a `DISTINCT` line for each Canadian depositary receipt (CDR) the books held whose root is a US ticker. taxjson never joins two listings because their letters match (only a broker's journal evidence or a `TOBASE` line joins), and the interlisted master itself keeps a CDR apart from its US share (no journal join, no `ticker-map --suggest` pair, no MAP-GAP, no loss-radar warning), so the lines say nothing. Nothing in the books changes when they go.
- **Fix:** `tjs update-tobase-map --write` removes the unedited ones (the previous file kept as `tobase.map.bak`). A line you edited is kept and listed under ATTENTION: delete it, or keep it (harmless). A `DISTINCT` line of your own in ticker.map stays as written.
- **Fixed in:** `v0.27.1`
- **Code:** `src/taxjson/lib/tobase_map.py` — `plan_update`, `DISTINCT_NOT_NEEDED`, `receipt_pairs`; `src/taxjson/lib/cross_listings.py` — `shown_apart`, `RECEIPT`; `src/taxjson/lib/checklist.py` — `s_tobase_map`

### "Error: two tobase.map files for this year: [settings] tobase_map = '../tobase.map' names ../tobase.map (shared by every year), and this folder holds a tobase.map of its own"
- **Check:** `ls 2025/tobase.map ../tobase.map` and `grep -n tobase_map 2025/taxjson.toml` (each year folder); `tjs years` (run in the folder holding the years) names the shared file and any year that keeps a copy.
- **Cause:** since v0.27.1 the years of a multi-year project read one `tobase.map` beside the year folders (`[settings] tobase_map`). A year folder that has the setting and still holds a copy of its own would leave one of the two silently unread, so every command refuses it (a copy restored from git, or a setting copied with `tjs align` into a year that kept its copy).
- **Fix:** `tjs migrate` in the folder holding the years (or `tjs -C .. migrate` from a year folder) makes the copies one shared file: identical copies at once, each kept as `tobase.map.bak`; copies that differ are listed and need `--write` (the newest year's file is kept). Or delete the year's own `tobase.map`, or remove its `tobase_map` setting (it then reads its own copy, the layout before v0.27.1).
- **Fixed in:** `v0.27.1`
- **Code:** `src/taxjson/lib/project_layout.py` — `tobase_both_problem`, `tobase_setting`, `setting_problems`; `src/taxjson/bin/taxjson_run.py` — `_refuse_folder_settings`, `_migrate_shared_tobase`

### A filed year: "Error: two tobase.map files for this year: … but this year is filed and its tobase.map is the copy close-year froze", or "filed 2024 was closed with its inputs frozen, but taxjson.toml names the shared inputs_dir (../inputs) and tobase_map (../tobase.map) again"
- **Check:** `grep -n "inputs_dir\|tobase_map" 2024/taxjson.toml` shows live lines besides the commented ones under `## frozen by`; `ls 2024/tobase.map*`. An older taxjson said "… and this folder holds a tobase.map of its own — `taxjson migrate` …" here, and after `tjs migrate` the year's run warned `filed 2024 DRIFTED` with "frozen input(s) changed … (restore them from your records)" although the frozen copy was untouched.
- **Cause:** `tjs years` counted a filed year's frozen `inputs_dir` and `tobase_map` (commented out by close-year) as settings that differ from the newest year's, and `tjs align --from 2025 --write` in the filed year wrote them back: the year read the shared exports again, not its frozen copy. The run's error then named `migrate`, which removed the frozen `tobase.map` (kept as `tobase.map.bak`). Now `years`, `years --diff` and `align` leave those two keys out whenever either year is frozen, the error names the lines to delete, and `migrate` never takes a filed year's frozen copy for one to merge.
- **Fix:** delete the live `inputs_dir` and `tobase_map` lines from the filed year's `[settings]` (leave the commented ones); if `migrate` removed its copy, `mv 2024/tobase.map.bak 2024/tobase.map`. Then `tjs run` there says `filed 2024: OK`. Do not `close-year --force` before that: it would lock books built from the shared exports.
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/lib/project_layout.py` — `_FROZEN_KEYS`, `frozen_project`, `compare`, `tobase_both_problem`, `tobase_layout`; `src/taxjson/bin/taxjson_run.py` — `_frozen_settings_back`, `_check_filed_years`, `cmd_align`

### "Info: filed 2023 was closed with its inputs frozen, but taxjson.toml names the shared inputs_dir (../inputs) again" in the 2024 folder, where deleting the line leaves "no account had any input"; or that message as an `Info:` before "Filed 2024: OK" in the filed year itself, with nothing in `tjs years`
- **Check:** `ls filed/` and `grep -n "^year" taxjson.toml` in the folder that says it. A lock of an earlier year (`filed/2023.json` in the 2024 project: a single-folder project that went on to the next year, then `tjs migrate --to-years`) is not the folder's own. The folder's own year: `grep -n "inputs_dir\|tobase_map" 2024/taxjson.toml` shows live lines beside the commented ones under `## frozen by`.
- **Cause:** the note was given for every lock closed with its inputs frozen, whatever its year: the 2024 project rightly reads the shared exports, and deleting `inputs_dir` as told left it reading an `inputs/` it does not have. For the year's own lock, the note was only an `Info:` (`run --strict` went on) and `tjs years` showed the year as clean.
- **Fix:** upgrade. An earlier year's lock: keep `inputs_dir` (if you deleted it, put `inputs_dir = "../inputs"` back); the run now says "filed 2023 is checked from the inputs this 2024 project reads". The year's own lock: the message is a `Warning:` (`run --strict` stops: "filed year(s) no longer read their frozen inputs") and `tjs years` names it ("frozen, but [settings] names inputs_dir again: delete it"); delete the live lines as the entry above says.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_check_filed_years`, `_frozen_settings_back`, `is checked from the inputs`, `no longer read their frozen inputs`; `src/taxjson/lib/project_layout.py` — `years_report`, `frozen, but [settings] names`

### `tjs years --details`: "tobase.map: a copy in each of 2024 — `taxjson migrate` here makes them one file every year reads" for a filed year; `tjs migrate` then says "nothing to migrate here"
- **Check:** the default `tjs years` says "tobase.map: frozen copy of its own in 2024 (filed)" for the same year; `2024/filed/2024.json` exists.
- **Cause:** the details view did not tell the copy close-year froze (the filed year's own by design) from a per-year copy of the layout before v0.27.1; the default view did.
- **Fix:** nothing to do: the frozen copy stays. After the upgrade `--details` says "frozen copy of its own in 2024 (filed: close-year froze it, the year reads it)".
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_years_tobase`, `cmd_years`, `_years_table`; `src/taxjson/lib/project_layout.py` — `years_report`, `tobase_frozen`

### `tjs run` in a filed year after `tjs align --from 2025 --write`: "Info: filed 2024: 1 frozen input(s) changed since the year was closed: taxjson.toml — the books no longer come from the exports as filed"
- **Check:** `ls 2024/taxjson.toml.bak 2024/ticker.map.bak` (the files `align` replaced); `tjs years --diff 2024 2025` lists what differs.
- **Cause:** `tjs years` showed the settings in which a filed year differs from the newest year as a step (`What differs: tjs years --diff 2024 2025`), and `align --write` in the filed year changed the taxjson.toml (or ticker.map) its lock was closed with, with no word about it. Now `years` shows those differences as the year's own (`1 setting(s) (filed)`, no step), and `align --write` in a filed year warns "2024 is filed: --write changes the taxjson.toml and ticker.map filed/2024.json was closed with" before it writes.
- **Fix:** to keep the year as filed, put the previous files back (`mv 2024/taxjson.toml.bak 2024/taxjson.toml`, the same for `ticker.map.bak` if align wrote one), then `tjs run` there: `filed 2024: OK`. For an amendment, keep the change and `tjs close-year --force` once the amended return is filed.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_years_as_filed`, `_years_table`, `_align_filed_year`, `cmd_align`

### `tjs years` while a filed year's lock is damaged: "! 2024: filed/2024.json cannot be read" and "! tobase.map: a copy of its own in 2024; make it the shared file — tjs migrate"
- **Check:** `grep -n "## frozen by" 2024/taxjson.toml` (the year was closed with its inputs frozen); `ls 2024/tobase.map*`.
- **Cause:** whether a year is frozen was read from its lock alone, so with the lock unreadable the copy of `tobase.map` close-year froze looked like a per-year copy to merge, and `tjs migrate` pointed the filed year at the shared file again (its copy kept as `tobase.map.bak`). A year whose taxjson.toml carries the freeze note now stays frozen while its lock is damaged: `years` says "frozen copy of its own in 2024 (filed)" and `migrate` leaves it alone.
- **Fix:** restore the lock from git (`git log -- 2024/filed/2024.json`). If `migrate` already ran: `mv 2024/tobase.map.bak 2024/tobase.map` and delete the live `tobase_map` line it set in 2024/taxjson.toml's `[settings]` (leave the commented one under the note), then `tjs run` there.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/project_layout.py` — `frozen_year`, `_freeze_noted`, `FROZEN_NOTE_PREFIX`, `tobase_layout`

### `tjs years`: "lock missing" and "! 2024: filed/2024.json is missing: restore it or run close-year again" (older releases: the year shown `open`, and "! tobase.map: a copy of its own in 2024; make it the shared file — tjs migrate")
- **Check:** `ls 2024/filed/` has no `2024.json`, while `grep -n "## frozen by" 2024/taxjson.toml` finds close-year's freeze note; `git log -- 2024/filed/2024.json` shows whether it was committed. `tjs check-filed` there says "filed 2024 could not be checked: filed/2024.json is missing" (exit 1).
- **Cause:** the lock was deleted (a `git clean`, a copy of the project without `filed/`), but the year still reads its own frozen copy of the exports: the freeze note and the commented-out `inputs_dir` / `tobase_map` stay in its taxjson.toml. Older releases read a year with no lock as never closed: `years` offered `tjs migrate`, which pointed the filed year at the shared tobase.map, and `new-year` copied the freeze note into the next year, which then read an empty `inputs/` of its own. Now the year stays frozen, as with a damaged lock: `years` shows `lock missing`, `migrate` leaves it alone, `check-filed` and `tjs run` warn, the checklist's lock steps are blocked, and `new-year` names the shared folders.
- **Fix:** restore the lock (`git checkout -- 2024/filed/2024.json`, or from a backup), or, after checking the year's figures against the return you filed, `tjs close-year` in 2024/ writes it again. A year you meant to reopen: delete the note and set `inputs_dir` / `tobase_map` again under `[settings]` (the commented lines under it). A next year started by an older `new-year` from such a year: delete the copied note and set `inputs_dir = "../inputs"` (and `tobase_map = "../tobase.map"`) in its `[settings]`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/project_layout.py` — `frozen_year`, `lock_missing_frozen`, `lock_missing_parts`, `years_report`; `src/taxjson/bin/taxjson_run.py` — `_years_table`, `_lock_missing_year`, `_check_filed_years`, `_align_filed_year`; `src/taxjson/lib/checklist.py` — `_lock_state`, `_filed_after_year_end`

### `tjs years` shows a filed year as clean while its `tjs run` says "Warning: filed 2024 DRIFTED vs 2024.json"
- **Check:** `tjs -C 2024 check-filed` lists the figures that moved.
- **Cause:** `years` read the lock's totals and the input fingerprint only; the run's drift check kept no verdict. Each drift check (`tjs run`, `tjs check-filed`) now keeps its verdict in `work/filed_check.state` with the lock it judged, and `years` shows `filed … DRIFTED` and "! 2024: its last run DRIFTED from filed/2024.json — tjs -C 2024 check-filed" (`drifted` in `--json`) until a check matches or the year is closed again.
- **Fix:** after upgrading, `tjs run` (or `tjs check-filed`) in the filed year records the verdict. Then as for "Warning: filed 2024 DRIFTED vs 2024.json": put the frozen inputs back, or amend the return and `tjs close-year --force`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/project_layout.py` — `record_filed_check`, `filed_check`, `FILED_CHECK_STATE`, `years_report`; `src/taxjson/bin/taxjson_run.py` — `_check_filed_years`, `_years_table`

### `tjs migrate`: "the year folders' tobase.map copies differ (listed above) — nothing was written for them"
- **Check:** the lines `tjs migrate` lists: "Lines of yours only 2024/tobase.map has" (your own lines and edited marked lines that the newest year's copy lacks) and how many master lines of another master version each copy has. `tjs migrate --dry-run` shows the plan without writing.
- **Cause:** the year folders of a project made before v0.27.1 keep a copy of tobase.map each, and they differ (an update run in one year only, or a line you added in one year). One file every year reads needs one text, and taxjson does not guess which of your lines to keep.
- **Fix:** `tjs migrate --write` keeps the newest year's file as the shared `tobase.map` (each copy kept as `tobase.map.bak`); then add any listed line of yours you still need to the shared file (or to that year's ticker.map, which wins over it), and run `tjs update-tobase-map --write` once to bring the shared file to the installed master. `tjs run` in each year.
- **Fixed in:** `v0.27.1`
- **Code:** `src/taxjson/lib/tobase_map.py` — `plan_shared`, `apply_shared`; `src/taxjson/bin/taxjson_run.py` — `_migrate_shared_tobase`, `cmd_migrate`

### `tjs migrate`: "Not changed: 2023, 2024 read no tobase.map (add the setting there to read the shared one)" for filed years, while `tjs years` says "frozen copy of its own in 2023, 2024 (filed)"
- **Check:** `ls 2024/tobase.map 2024/filed/2024.json`: the year is filed and holds the copy close-year froze.
- **Cause:** migrate counted a filed year's frozen copy as no tobase.map and told you to add the setting: following it points the filed year at the shared file, and its run then refuses the two files. It also said `set [settings] tobase_map …` before refusing copies that differ.
- **Fix:** upgrade: migrate says "Not changed: 2024 reads its own frozen copy (filed: close-year froze it)" and "would set" until it writes. If you added the setting to a filed year, delete it again (the year reads its frozen copy).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/tobase_map.py` — `plan_shared`, `SharedPlan`; `src/taxjson/bin/taxjson_run.py` — `_migrate_shared_tobase`, `own frozen copy`

### `tjs migrate`: "the year folders' tobase.map cannot be made one file:" with "2025/tobase.map is not UTF-8 text (byte 0xff at offset 0) — save it as UTF-8 in your editor first"
- **Check:** open the named year's `tobase.map` (or the shared `tobase.map`): a file saved in another encoding, or a damaged one. `tjs run` in that year refuses it too ("not UTF-8 text"). v0.35.0 went on, after the error, to print the plan naming the refused file "the one kept" (nothing was changed: ignore it), and named an older year's copy without the byte and offset; both are fixed in the release after it.
- **Cause:** the newest year's copy becomes the one file every year reads. A copy that was not UTF-8 text was compared as an empty map (and, when every copy held the same bytes, not looked at) and would have been written for every year; on an older release the migration went through.
- **Fix:** save the file as UTF-8 in your editor (a damaged copy: restore it from `tobase.map.bak` or git), then `tjs migrate` again. The refused run changed nothing.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/tobase_map.py` — `plan_shared`, `is not UTF-8 text`; `src/taxjson/bin/taxjson_run.py` — `_migrate_shared_tobase`

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

### `tjs format-map --write` moves the commented-out examples under `## --- Lookups ---` (`# QUOTE XYZQ.TO XYZQ.V` … `# MULT ZZQ1 50`) above a line you appended at the end of ticker.map
- **Check:** `diff ticker.map.bak ticker.map` shows the eight `taxjson init` examples removed from the Lookups group and added directly above your new line in its own group.
- **Cause:** a commented-out rule directly above a line moves with it like a comment, and the template's Lookups examples end the file `taxjson init` writes, so a line appended at the end sat directly under them.
- **Fix:** upgrade: the examples `taxjson init` writes stay in their keyword's group wherever they sit (a commented-out rule of your own still moves with the line below it). On an older release, leave a blank line before the line you append.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/ticker_map_format.py` — `_items`, `_INIT_EXAMPLES`

### "Warning: [settings] option_grant_timing_since is not set, so grant timing (ITA s.49(1)) starts at the project year (2025)"
- **Check:** shown by `tjs run` (after the books are built) in a Canadian project on grant timing (the default `option_premium_timing`) whose taxable books hold a written option; a project without one is not warned (`tjs init` leaves the key commented).
- **Cause:** without the key, grant timing starts at `year`, which moves when you bump `year` next spring: last year's year-straddling written options would go back to close timing and their premium would be taxed twice. See `tjs option-boundary` and `tjs tax-logic`.
- **Fix:** add `option_grant_timing_since = 2025` (the first year you file under grant timing) to `[settings]` once, and keep it unchanged in every later year's project. `tjs new-year` does this for the new year from a previous year that left it unset (on an older release it did not, and the new year taxed such a premium again: set it to the previous year by hand).
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_grant_since_warning`, `option_grant_timing_since is not set`, `_implicit_grant_since`

### `tjs checklist`: "! 24 option-boundary: option_grant_timing_since is not set in [settings]…" in a project that writes no options
- **Check:** `tjs option-boundary --json` has `"rows": []` and `"since_warning": false`; `tjs run` gives no grant-timing warning.
- **Cause:** the checklist flagged the setting whenever it was unset, though option-boundary and the run warn only when the books hold a written option (with none, the default moves no premium). Now it follows their warning (`since_warning`).
- **Fix:** upgrade; nothing to set unless you write (sell to open) options.
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/lib/checklist.py` — `d_option_boundary`, `since_warning`; `src/taxjson/bin/taxjson_run.py` — `cmd_option_boundary`, `since_warning`

### Crypto only, in the `tjs init` scaffold: "Warning: [settings] option_grant_timing_since is not set, so grant timing (ITA s.49(1)) starts at the project year"
- **Check:** the run says "skipping account 'margin'" (the scaffold's taxable account, configured but empty): no taxable equity books, so no written option.
- **Cause:** the warning was left out only when taxable equity books were read and held no written option; with no such books at all it was said anyway.
- **Fix:** nothing to set (or delete the empty account sections). Since the fix a project with no taxable equity books is not warned.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_grant_since_warning`, `_books_write_options`

### "Warning: option_grant_timing_since = 2026 is after year = 2025: 2025's written options are taxed at the close (right only if 2025 was filed that way)"
- **Check:** `grep -n option_grant_timing_since taxjson.toml`: usually `year` was lowered (filing an earlier year) and the key kept the value an earlier `taxjson init` wrote.
- **Cause:** contracts written before `option_grant_timing_since` keep close timing, so every option written in the project year is taxed when it closes, not when written (ITA s.49(1)). A `taxjson init` before this release wrote the key set to the init year in every new project.
- **Fix:** set it to the first year you file under grant timing (or delete the line for the project year); keep it unchanged in later years. When the year was filed on close timing (a reconstruction of a return filed that way), the value is right: nothing to do.
- **Fixed in:** `v0.28.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_grant_since_after_year`, `_books_write_options`, `_grant_since_warning`; `src/taxjson/lib/config_template.py` — `scaffold_document`

### "Warning: no transaction in any account's books is dated 2021 (the books run 2025-01-10 to 2025-12-31)"
- **Check:** `tjs run --details` adds "Every 2021 filing total will be 0. Is [settings] year in taxjson.toml right?"; the run itself finishes.
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

### "Error: folder(s) that are symlinks to outside the project — taxjson writes there; nothing was run: work/ -> /mnt/scratch/work"
- **Check:** `ls -ld work reports filed export inputs inputs/*` in the project shows the named folder as a link (`->`) to a path outside the project folder. Every command run in the project stops with exit 2 before it reads or writes anything (`tjs init` checks the folders it creates itself; `tjs redact` writes only its own `inputs_redact/` and runs).
- **Cause:** taxjson writes your books (`work/`), reports, the filed lock and, in an account's `inputs/` folder, the elections and crypto-send decisions. A folder that is a link leaving the project sent those files wherever it points, so such a link is refused, as a `ticker.map` link outside the project is. A link to a folder inside the project is fine.
- **Fix:** replace the link with a real folder: `rm work && mkdir work` (copy the contents in first if you need them; `work/` is rebuilt by `tjs run`), or run taxjson in the folder the link points into.
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_refuse_outside_links`, `_refuse_outside_dir_links`, `_WRITTEN_DIRS`, `load_config`; `src/taxjson/lib/safe_write.py` — `link_outside`

### `tjs watch` wrote `work/.watch_state.json` through a `work/` link to outside the project; `tjs list`, `audit`, `wash-radar` and others ran with the link in place
- **Check:** `ls -ld work reports inputs/*` shows a link (`->`) leaving the project; `tjs run` and `tjs sum` stop with "folder(s) that are symlinks to outside the project", but `tjs watch` printed its baseline and `.watch_state.json` appeared in the link's target, and the listings (`list`, `events`, `gains` ...), the radar family, `audit`, `wash-sales`, `stats`, `find-missing-history`, `format-map` and `years` ran at exit 0.
- **Cause:** the refusal sat in the config loader most commands read the project through; about 35 commands read taxjson.toml their own way (the radar's reader, the listings' soft read) and never reached it, and watch then saved its state through the link (audit 2026-10-25 robustness#3).
- **Fix:** replace the link with a real folder (`rm work && mkdir work`; `tjs run` rebuilds it), and delete the stray `.watch_state.json` from where the link pointed. Upgrade: one check before any command, whatever reads its config, refuses the link (exit 2), as the entry above says. A link to a folder inside the project still runs.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_refuse_outside_links`, `_LINK_CHECK_FREE_CMDS`, `_refuse_outside_dir_links`, `cmd_watch`

### "Error: '-C sum' is not a valid command in this chain — nothing was executed." from `tjs -C run run sum`
- **Check:** the folder given to `-C` (or `--dir`) has the name of a taxjson command (`run`, `sum`, `events` ...); `tjs -C run checklist` with one command fails the same way, or `tjs -C run` alone stops with "the following arguments are required: COMMAND" instead of showing the help page.
- **Cause:** command chaining looked for the first word that names a command, and took the folder after `-C` for it: the project folder was lost and the first command's name became the folder.
- **Fix:** upgrade: the value of `-C` / `--dir` is always the folder. On an older release, write the folder another way (`tjs -C ./run run sum`, or an absolute path).
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_command_index`, `_takes_separate_value`, `_split_command_segments`, `_no_command`

### `tjs events sum` prints the gains summary for an account named `sum` (or "note: 'sum' starts a new chained command; the previous command ('events') could also have taken it as an argument"), or `tjs init sum` initializes the current folder
- **Check:** taxjson.toml has an `[accounts.NAME]` whose NAME is also a command (`sum`, `trades`, `run` ...), or the folder given to `init` is named like a command.
- **Cause:** chaining (`tjs run sum`) started a new command at every command name after a complete command, so an optional account or folder named like a command ran that command instead, and `tjs events -- sum` chained too: no spelling addressed the account.
- **Fix:** upgrade: a command name starts the next command only when the command before it cannot take the word, and an account of the project, a folder (`init`) or a symbol (`audit`) is the command's own (`tjs events sum` lists the events of account sum). To chain after such a command, separate the two with `--` (`tjs events -- sum`). On an older release, rename the account (its `inputs/` folder too), or give `init` the folder as `./sum`.
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_split_command_segments`, `_chain_accounts`, `_CHAIN_ACCOUNT_DESTS`

### `tjs init`: "Error: folder(s) that are symlinks to outside the project — init writes there; nothing was written: inputs/ -> ../shared"; or, on an older release, init put the account folders and their README.txt files in the folder `inputs/` links to
- **Check:** `ls -ld inputs 2024` in the folder you ran init in shows a link (`->`) to a folder outside it.
- **Cause:** init created the account folders under `inputs/` (and the year's folder, `holdings/`) through such a link, outside the project, and every later command then refused the project for that link. It now checks every folder it would write into first, as every other command does.
- **Fix:** replace the link with a real folder (move its contents in), or run init in the folder the link points into. A link to a folder inside the project is fine.
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_init`, `_init_refuse_outside_links`; `src/taxjson/lib/safe_write.py` — `link_outside`

### "Warning: the project folder, inputs/ can be read by other users: `chmod -R go-rwx` it"
- **Check:** `tjs run --details` says it in full: "the project folder, inputs/ can be read by other users of this computer (made by an older taxjson or another program; new files are owner-only)" and "Tighten it once: chmod -R go-rwx <project>". `ls -ld . inputs reports` in the project: a mode other than `drwx------` (for example `drwxrwxr-x`) on any of them. The warning shows once per `tjs run`.
- **Cause:** taxjson creates every folder and file owner-only (0700 / 0600), but folders made by an older release, by `mkdir`, `git clone` or a copy keep your shell's permissions, and other accounts on the machine can then list or read your statements and books.
- **Fix:** run the command the warning names once: `chmod -R go-rwx <project>`. Nothing else changes; the warning stops.
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_loose_project_dirs`, `can be read by other users`

### `tjs run`: `[carryover] claimed: 2023 is given twice`
- **Check:** look in taxjson.toml `[carryover] claimed` for one year written two ways (`2023` and `02023`).
- **Cause:** TOML keeps both keys, but they are one tax year; older releases silently used one of the two amounts.
- **Fix:** keep one entry for that year with the amount claimed on that return.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/project_tables.py` — `claimed_losses`, `table_problems`

### `ticker.map.bak` (or `tobase.map.bak`, a `.tt` file's `.bak`) holds only one of two versions after two commands rewrote the same file at the same moment
- **Check:** two taxjson commands that rewrite the file (`tjs format-map --write`, `tjs migrate`, `tjs update-tobase-map --write`) ran at once (one of them from cron or a script); `ls -l ticker.map.bak*` shows one backup where two versions were replaced.
- **Cause:** the backup helper picked the first free `.bak` name and only then wrote it; two callers that both saw `.bak` free both wrote it, and the second overwrote the first's copy.
- **Fix:** upgrade: a backup name is reserved with an exclusive create (choosing the name and taking it are one step), so the second caller takes `.bak1`. The version an older release overwrote cannot be recovered; the file's current contents are what the last command wrote.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/safe_write.py` — `backup_copy`, `_same_regular_file`, `_fill`

### `tjs estimate` / `tjs amt`: "error: [estimate] amt_carryover: 2023 is given twice ('2023' and '02023') — one entry per year of origin"
- **Check:** look in taxjson.toml `[estimate] amt_carryover` for one year of origin written two ways (`2023` and `02023`, or quoted and bare).
- **Cause:** TOML keeps both keys, but they are one year of origin; older releases silently kept the second amount (2026-10-24 audit A39).
- **Fix:** keep one entry for that year, with the unapplied minimum-tax carryover the notice of assessment / T691 shows.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/carryforward.py` — `amt_from_config`

## Reading the broker files

### "Error: cannot detect broker for inputs/qt/99900001.csv. Check the header first: …"
- **Check:** `taxjson-detect-brokerage inputs/qt/99900001.csv` prints `unknown` and the same advice. `tjs run` prints one `Info: File … → identified as …` line for each file it could route; this file has none.
- **Cause:** the file's content matches no supported export's header (IB, Questrade, Webull, RBC Direct, Coinbase, Kraken), no generic mapping sits beside it, and its name has no `cb_`/`kr_` fallback. Usually it comes from a broker taxjson has no parser for, or it is not an activity export.
- **Fix:** download the broker's activity export in its standard CSV layout. For another broker, write a column mapping named `99900001.csv.toml` next to the file (start from `examples/generic_wealthsimple.toml`). Rename a file to `cb_…`/`kr_…` only for a Coinbase or Kraken export whose header is not recognised.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_detect_brokerage.py` — `cannot_detect_message`, `cannot detect broker for`; `src/taxjson/lib/brokerages/detect.py` — `content_matches`; `src/taxjson/bin/taxjson_run.py` — `group_inputs_detailed`

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

### "Error: inputs/qt/99900001.csv is empty — a failed or interrupted download?" or "Error: inputs/qt/99900001.csv: not UTF-8 text (byte 0xe9 at offset 3)" (older releases: the file's absolute path, often on a line of its own under a bare "Error:")
- **Check:** the file is 0 bytes or blank lines only, or a spreadsheet or editor saved it in another encoding (Windows-1252 "CSV (Comma delimited)"). A `.tt` file, `ticker.map` or `taxjson.toml` you typed says "save it as UTF-8 in your editor" instead.
- **Cause:** taxjson reads UTF-8 (with or without BOM) and UTF-16 (with a BOM) only. An empty file is a failed download; renaming either one never helps.
- **Fix:** download the export again. If you must edit it, save it as "CSV UTF-8". A file you typed: save it as UTF-8 (in most editors: Save As, encoding UTF-8).
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `group_inputs_detailed`, `is empty — a failed or interrupted`, `_stage_text_shown`; `src/taxjson/lib/cli_diag.py` — `not_utf8`, `utf8_fix`, `not UTF-8 text`

### "Error: taxjson.toml:28: not valid TOML: Expected newline or end of document after a statement (column 13)" (older releases: "…/taxjson.toml is not valid TOML: … (at line 28, column 13)")
- **Check:** open `taxjson.toml` at the line it names: a value with something after it (`year = 2024 =`), a string without its quotes, a table header twice.
- **Cause:** every command reads `taxjson.toml` first, and a file that does not parse is never guessed around (a missing key would silently change a figure).
- **Fix:** correct that line (docs/settings.md lists every key), then run the command again.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `load_config`, `_toml_problem`, `_config_shown`, `_TOML_FIX`

### "Error: spreadsheet export(s) in inputs/ are NOT read — only .csv and .tt files are, …"
- **Check:** the lines under it list each spreadsheet, e.g. `inputs/qt/activity.xlsx`.
- **Cause:** only `.csv` and `.tt` files are read. A `.xlsx`, `.xls`, `.xlsm`, `.ods` or `.numbers` file in an inputs folder would leave every trade in it out of the books. Before v0.17.0 such a file was skipped with no message.
- **Fix:** convert it (`taxjson-xlsx-to-csv FILE.xlsx -o FILE.csv`, or download the broker's CSV) and move the spreadsheet out of `inputs/`. A spreadsheet next to its own CSV conversion (same name) is only a warning.
- **Fixed in:** `v0.17.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `SPREADSHEET_SUFFIXES`, `spreadsheet export(s) in inputs/ are NOT read`

### `taxjson-xlsx-to-csv FILE.xlsx -o FILE.csv` where FILE.csv is a symlink: the file the link pointed at was overwritten with the CSV
- **Check:** `ls -l FILE.csv`. On the current release the name is a regular file holding the CSV and the link's former target is unchanged; on an older release the name is still a link and its target holds the CSV.
- **Cause:** the CSV was written through pandas' own writer, which opens the output name and follows a symlink there, so a link at the output name (planted, or one you made yourself) had its target overwritten (audit 2026-10-24 B06). Every other generated file is staged beside its name and renamed into place (`lib/safe_write`), which replaces the link and never opens its target; the conversion now writes the same way.
- **Fix:** upgrade. If you want the CSV where the link points, give `-o` that path.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/xlsx_to_csv.py` — `convert_xlsx_to_csv`, `write_atomic`

### "Warning: qt: inputs/qt/99900001.csv parsed to 0 transactions — NONE of its rows are in the books"
- **Check:** the line before it, `Warning: 99900001.csv parsed to 0 transactions (155 bytes input, brokerage=questrade)`, names the parser that read the file. `tjs run --strict` stops on it.
- **Cause:** the header was recognised but no row became a transaction: a header-only export (a date range with no activity), or a file whose rows that parser does not book.
- **Fix:** check the export's date range and download it again, or remove a header-only file from `inputs/<account>/`.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_brokerage.py` — `parsed to 0 transactions`; `src/taxjson/bin/taxjson_run.py` — `_ZERO_TX_RE`

### "Warning: inputs/margin/book.tt:1: a .tt line's total 110.00 is not qty x price + fee = 100.00"
- **Check:** `tjs run --details` quotes the `.tt` line under it (`BUYSELL 2025-01-10 10:00:00 QZA.TO 10 CAD 10 110`); `tjs checklist`'s run-clean step lists it ("1 .tt line(s) whose total is not qty x price +/- fee, booked as written"), and `tjs run --strict` stops on it. A sale whose commission exceeds its gross, written with a 0 total, reads "a .tt sale's total is 0 but its commission exceeds its gross".
- **Cause:** a `.tt` BUYSELL line's total is what the engine books — a purchase's cost, a sale's proceeds — not quantity x price. A total more than 1% (at least 0.05) off quantity x price (x the contract size) + fee for a purchase, − fee for a sale, is usually a typo; it is booked as written. Before the fix the warning reached only the `.sum` DIAGNOSTICS, so a wrong cost or proceeds went through with nothing on the console, `--strict` and the checklist saying nothing. A line with no price (0: the total alone states the amount) and a futures line without its size are not compared.
- **Fix:** correct the total (or the quantity or price) and `tjs run`. If the total is right as written (a charge the line does not show), put the difference in the line's fee column so the two agree. An `ACQUIRED` line ("a .tt ACQUIRED line's total 530.00 is not qty x price = 480.00") has no fee column: write the price the message gives (the total divided by the quantity). Earlier the warning on an `ACQUIRED` line quoted the `BUYSELL` it expands to and advised the fee column, and the warning and the `--strict` stop named the file unmasked while the "Reading" step line masked an account-number-like part of the name; all three now name it the same (masked) way.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/tt_totals.py` — `read_diag`, `project_mismatches`, `tolerance`, `source_line`, `a .tt line's total`, `ACQUIRED has no fee column`; `src/taxjson/bin/taxjson_convert_tt.py` — `.tt line total`; `src/taxjson/bin/taxjson_run.py` — `_echo_tt_totals`; `src/taxjson/lib/checklist.py` — `d_run_clean`

### "Warning: inputs/margin/extra.tt:2: QZA has no market suffix: a pool of its own, apart from QZA.US in the books"
- **Check:** the `.tt` line names the share without its market suffix (`QZA`), while the broker's rows book it as `QZA.US` or `QZA.TO`; `tjs list` shows both, the broker's sale of the suffixed listing going short.
- **Cause:** a share listing is spelled with its suffix in the books; a bare symbol is its own ACB pool (a coin's spelling). Before the fix the warning reached only the account's `.sum`, and with exports shared by every year (`inputs_dir`) not even that.
- **Fix:** write the symbol as the warning names it (`QZA.US`) on that `.tt` line and `tjs run`.
- **Fixed in:** `v0.28.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_echo_tt_bare_symbols`; `src/taxjson/bin/taxjson_convert_tt.py` — `_warn_bare_equity_symbol`, `--equity`

### "Error: hand.tt:1: BUYSELL needs PRICE and TOTAL after CUR (BUYSELL DATE TIME SYMBOL QTY CUR PRICE TOTAL [FEE] [xSIZE])" (older releases: "malformed .tt line (IndexError: list index out of range)")
- **Check:** `tjs check-tt` names the line with the same message; the line has fewer fields than its action's form (the form is in the message, and in docs/settings.md, ".tt files").
- **Cause:** a field was left out (often the price or the total of a hand-typed trade). Earlier releases indexed past the end of the line and printed the Python error and the line with its line end (`'…\n'`) instead of the fields it lacks.
- **Fix:** write the missing fields, in the form's order, and `tjs check-tt` again.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_convert_tt.py` — `parse_tt_line`, `_FORMS`, `needs`

### "Error: case.tt:1: SPLIT ratio must be > 0 (got 0)" (older releases: "taxjson-merge2(…/work/margin_tt_case.json) Transaction at index 0 (…): SPLIT ratio must be > 0 (got 0.0)", after `tjs check-tt` said "Every line parses.")
- **Check:** the `.tt` line `SPLIT DATE TIME OLD NEW RATIO` has a RATIO of 0 or below; `tjs check-tt` names it.
- **Cause:** RATIO is new shares per old (`2` for two-for-one, `0.1` for one-for-ten, `1` for a rename): never 0 or negative. Earlier releases read the line and refused the row only later, when the account's books were merged, naming the converted work file instead of the line; `tjs check-tt` never reached that check.
- **Fix:** write the ratio as new shares per old (a consolidation is a ratio below 1, never a negative one).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_convert_tt.py` — `parse_tt_line`, `SPLIT ratio must be > 0`; `src/taxjson/lib/core.py` — `coerce_transaction_row`

### `tjs check-tt`: "inputs/margin/hand.tt:4: unknown .tt action 'second'" for a word inside a comment, or line numbers past the run's ("hand.tt:8" where the run says "hand.tt:6")
- **Check:** the file holds a form feed, a vertical tab or a Unicode line or paragraph separator (U+2028, U+2029, U+0085) inside a line, often pasted into a comment: `grep -nP '[\x0b\x0c\x{85}\x{2028}\x{2029}]' inputs/margin/hand.tt`. On the current release `tjs check-tt` numbers the lines as `tjs run` and an editor do.
- **Cause:** a `.tt` line ends at a line feed only, as `tjs run` reads it. Earlier releases of `tjs check-tt` also split at those characters, so every later line was numbered one more, and the text after one in a comment was read as a line of its own.
- **Fix:** upgrade. The characters do no harm inside a comment; elsewhere on a line, delete them.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/check_tt.py` — `check_file`; `src/taxjson/bin/taxjson_convert_tt.py` — `tt_to_json`

### A `RENAME`, `JOURNAL`, `ALLOWLOSS`, `INKIND`, cash or `OPENING ... cost=unknown` line inside a `.tt` comment is booked ("ATTENTION: 1 trade(s) in ZZQA.TO after its rename to ZZQB.TO"), or a message names the line after the one an editor shows
- **Check:** `grep -nP '[\x0b\x0c\x1c-\x1e\x{85}\x{2028}\x{2029}]' inputs/*/*.tt` finds a form feed, a vertical tab or a Unicode line or paragraph separator inside a line, often pasted into a comment from a web page or a word processor. `tjs run` on the current release reads the text after it as part of the comment.
- **Cause:** a `.tt` line ends at a line feed only, and `tjs run` books the line before the `#`. Earlier releases' readers of the declared lines (`RENAME`, `JOURNAL`, `ALLOWLOSS`, `INKIND`, the FX-on-cash lines `FXCONV`, `CASHMOVE`, `CASHOPEN`, `CASHBAL` and `CASHBOOK`, and the `OPENING ... cost=unknown` list) also split at those characters: the text after one in a comment was read as a line of its own and booked, and their messages named every later line one more.
- **Fix:** upgrade, then `tjs run` again. A rename, journal, loss override, in-kind move or cash line that was only in a comment is no longer booked, so the positions, ACB, a superficial-loss or wash-sale denial, an in-kind move's values and the FX gain or loss on cash are figured without it. A line you meant goes on a line of its own.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_convert_tt.py` — `tt_lines`; `src/taxjson/lib/dated_events.py` — `read_declarations`; `src/taxjson/lib/missing_history.py` — `read_tt_openings`; `src/taxjson/lib/loss_overrides.py` — `read_project`; `src/taxjson/lib/cash_events.py` — `read_project_lines`; `src/taxjson/bin/taxjson_run.py` — `_inkind_lines`

### `tjs check-tt inputs/margin/x.tt` (a file named by its path): "Every line parses." while `tjs check-tt margin` and `tjs run` refuse a line of it ("is already opened at unknown cost", "a .tt file name may not end in '_base'")
- **Check:** `tjs check-tt margin` (the account's name) lists the problem with the file and line.
- **Cause:** earlier releases read a file named by its path line by line only; the checks across the account's lines (a symbol opened twice at an unknown cost, two `RENAME` declarations that disagree, a `TRANSFER` a taxable account cannot hold) and the file-name check ran only for an account. A file the run reads for its account now gets them all, and a problem of another file of the account that names it is listed too. `--account` naming another account, or `--crypto` / `--equity` saying another kind than taxjson.toml, reads the file's lines alone, as before.
- **Fix:** upgrade, or check the account (`tjs check-tt margin`).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/check_tt.py` — `check_paths`, `_as_in_project`, `_reads`, `project_root`

### `tjs check-tt inputs/margin/x.tt` in a year folder, or `tjs -C 2024 check-tt ../inputs/margin/x.tt`: "Error: inputs/margin/x.tt: no such file"
- **Check:** the file is where `tjs check-tt` (no argument) lists it: `inputs/margin/x.tt` is under the folder holding the year folders in the default layout (`inputs_dir = "../inputs"`).
- **Cause:** earlier releases looked for a relative FILE from the shell's folder only, so neither the names check-tt prints nor a path relative to the `-C DIR` project was found. A relative FILE is now looked for from the shell's folder, then from the project's, and a name of the form `inputs/<account>/x.tt` in the project's inputs folder.
- **Fix:** upgrade; on an older release give the path from the shell's folder (`../inputs/margin/x.tt` from a year folder).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_check_tt`, `_tt_file`, `_project_file`

### `tjs checklist`: "! 17 tt-lines: 2 .tt problem(s) `tjs run` refuses — inputs/margin/bad.tt:1: date... — tjs check-tt" (older releases: "[?] 17. tt-lines … 1 .tt file(s) in inputs/" while `tjs run` stopped on that line)
- **Check:** `tjs check-tt` lists every problem with its file and line; the checklist names the first.
- **Cause:** `tjs checklist --run` reads the `.tt` files as `tjs check-tt` does (nothing is written): a line the run refuses makes the step attention. Earlier releases only counted the files, so the step stayed a step to review and the other steps pointed back at `tjs run`, which failed on the line.
- **Fix:** fix each line `tjs check-tt` names, then `tjs run`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/checklist.py` — `s_tt_lines`; `src/taxjson/lib/check_tt.py` — `check_project`

### Generic importer: "Error: generic_ws.csv: generic importer: no mapping for generic_ws.csv" or "Error: …: generic importer: generic_ws.csv: mapped column(s) not in the CSV header: action -> 'Transaction type'."
- **Check:** a `generic_*.csv` (or a CSV with a `<file>.csv.toml` sidecar) is in `inputs/<account>/`. The second error lists the file's real header after `Header:`.
- **Cause:** the generic importer reads only through a mapping: the CSV's own `<file>.csv.toml`, or the folder's shared `generic.toml` for `generic_*` files. Each `[columns]` value must be a header name exactly as the CSV spells it (case aside).
- **Fix:** copy `examples/generic_wealthsimple.toml` to `inputs/<account>/<file>.csv.toml` and set each `[columns]` entry to your export's header (here `action = "Type"`). Unknown keys, a missing currency and similar mistakes each stop the run with their own message.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/generic.py` — `_load_mapping`, `generic importer: no mapping for`, `not in the CSV header`

### Generic importer: "Error: ws.csv: generic importer: ws.csv line 2: unparseable date '2024-02-01': it does not match [formats].date = '%d/%m/%Y' (ws.csv.toml)" (older releases: "… ws.csv: unparseable date '2024-02-01' with [formats].date='%d/%m/%Y'", with no line)
- **Check:** compare the date cell on the line named with `[formats].date` in the mapping named (a `strptime` pattern: `%Y-%m-%d` reads 2024-02-01, `%d/%m/%Y` reads 01/02/2024).
- **Cause:** the mapping's date format is not how the export writes its dates; a date is never guessed, so the file is refused. Earlier releases named neither the line nor the mapping.
- **Fix:** set `[formats].date` in the mapping to the export's shape, or correct the cell when only some rows are written another way, and `tjs run`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/brokerages/generic.py` — `unparseable date`, `it does not match [formats].date`

### "Error: questrade_demo.csv line 3: BUYSELL (no symbol) 2024-03-20: missing required field 'symbol'" (older releases: "Error: questrade_demo.csv (row id 25***): BUYSELL  2024-03-20: missing required field 'symbol'")
- **Check:** the line named in the export: the cell the field comes from (here the Symbol) is empty. `tjs run` stops at reading the account's broker files.
- **Cause:** a parsed row lacks a field every row of its kind needs, so it cannot be booked. The check runs after the parse, and it used to name the row by a masked content id, which cannot be searched for in the file; the Questrade and generic parsers now say which line the row came from (the others still give the masked id).
- **Fix:** fill in the cell (or download the export again), or enter the event as a `.tt` line and delete the row from the export, then `tjs run`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/brokerages/schema.py` — `_who`, `missing required field`; `src/taxjson/lib/brokerages/base.py` — `note_row_line`, `row_line`; `src/taxjson/bin/taxjson_brokerage.py` — `row_lines`

### "Error: questrade_demo.csv line 8: the header repeats here — two downloads pasted into one file, and 6 row(s) are in both (line 9 repeats line 2), so they would be booked twice" (Webull: "the statement preamble (Account Number) repeats here"; RBC: "rbc_direct_demo.csv:17: the header repeats here"); or, on an older release, a pasted file's rows booked twice
- **Check:** open the file: the line named starts a second copy of the export's header (Webull: its `Account Number` line), and rows above it appear again below it. On an older release: `tjs sum` shows the account's gain doubled or too high, Webull with no message, Questrade with "the export holds rows of 2 Questrade accounts (… Ac*** (Account Type))", RBC with "Amount 'Amount' is not a number".
- **Cause:** two downloads were pasted into one file. Inside one file two identical rows are two fills, so the rows both downloads hold were booked twice; two separate files' overlap is read once. The repeated header's `Account #` cell was also read as a second Questrade account. Two pasted parts that share no row are read as they are (each part in its own date order); RBC refuses any repeated header.
- **Fix:** split the file into one file per download (or download the whole range once) in the same `inputs/<account>/` folder and `tjs run`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/brokerages/base.py` — `refuse_pasted_overlap`, `PASTED_EXPORTS_FIX`; `src/taxjson/lib/brokerages/questrade.py` — `_read_qt_rows`, `_is_qt_header`, `_check_account_mix`; `src/taxjson/lib/brokerages/webull.py` — `_parse_rows`; `src/taxjson/lib/brokerages/generic.py` — `pasted`; `src/taxjson/lib/brokerages/rbc_direct.py` — `read_rbc_rows`, `the header repeats here`

### "Warning: UNBOOKED: …", then `tjs run --strict` stops: "Error: --strict: margin: ib input has event(s) the parser could not book — aborting"
- **Check:** each `Warning: UNBOOKED:` line names the file, the row and what was not booked; `work/<account>_<broker>.json.diag` keeps them. Common ones: IB `1 unhandled Corporate Action row(s) in U1234567.csv for: QZQ.` (a delisting, a rights issue), a generic mapping's `action 'REINVEST' (not in [actions]) carries a quantity/amount`, and a Webull `DIV` row (its own entry below).
- **Cause:** a row moves shares or cash, but the parser has no booking for it. The position or the income is wrong until it is entered by hand. `run --strict` refuses to publish while any such line is in a parse.
- **Fix:** book the event by hand in a `.tt` file in the account's folder (BUYSELL, SPLIT or DIVIDEND lines). For a generic mapping, map the action in `[actions]` (or to `"skip"` if it is not an event); that removes the line. A parser's UNBOOKED line stays while the row is in the export, even after the `.tt` booking, so `--strict` keeps stopping on it.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `UNBOOKED_PREFIX`, `unbooked_lines`, `parser could not book — aborting`; `src/taxjson/lib/brokerages/ib_extractor.py` — `unhandled Corporate Action`; `src/taxjson/lib/brokerages/generic.py` — `carries a quantity/amount`

### "Warning: broker account #ab12cd is in both qt and rrsp: keep its exports in one inputs/ folder" or "Warning: the same export file sits in two accounts: inputs/margin/a.csv, inputs/tfsa/a.csv (identical content)"
- **Check:** `tjs run --details` words the first as "the same broker account (#ab12cd) feeds two taxjson accounts, qt and rrsp (3 identical row(s))". Look at which `inputs/<account>/` folders hold that broker account's exports (the first message hashes the broker account; the second names both copies).
- **Cause:** one broker account's exports, or one downloaded file, sit in two taxjson accounts, so every row is booked in both.
- **Fix:** keep each broker account's exports under ONE `inputs/<account>/` folder; delete the copy in the wrong folder. `tjs run --strict` stops on either.
- **Fixed in:** `v0.17.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_warn_shared_broker_accounts`, `_duplicate_input_files`, `the same export file sits in two accounts`

### "Warning: Duplicates: a.csv and b.csv both hold 1 identical row(s)" … "Booked ONCE (read as the same row exported twice)", or "Warning: Duplicates: margin_extra.tt line (…) repeats the exported row in ib_2025.csv" … "so BOTH are booked"
- **Check:** the warning names both files and the row; the verdict ("Booked ONCE …", "so BOTH are booked") is in `tjs run --details` when the line is wider than the terminal; `tjs trades` for that day shows what was booked.
- **Cause:** exports carry no row id. An identical row in two overlapping exports of one account is read as the same trade exported twice, and the run says so when the files' overlap cannot prove it (they share only that row). A hand-kept `.tt` line that repeats an exported trade has no matching id, so both are booked.
- **Fix:** if the two export rows were really two trades, enter the second as a `.tt` line. If the `.tt` line is the exported trade, delete it. Overlapping downloads are otherwise fine to keep.
- **Fixed in:** `v0.17.0`
- **Code:** `src/taxjson/bin/taxjson_sort.py` — `plan_dedup`, `_tt_near_duplicates`, `Booked ONCE (read as the same row exported twice)`

### `tjs run`: generic importer "BUYSELL F:ZQX.US …: trade net_amount must be >= 0 (got -5000.0) Direction belongs in the quantity sign" on a futures buy at a negative price
- **Check:** the refused row is a futures symbol (`/…`, `F:…`) bought at a negative price (signed or unsigned quantities, an Amount of either sign).
- **Cause:** the generic importer stored the futures price unsigned, so the check that lets a plain-futures buy at a negative price receive cash never saw the negative price and refused the row. It now keeps a futures price's sign, as the IB parser does, and the native-currency view (`tjs gains`) books the buy at its negative cost (it showed a positive cost there, while the settled tax books were right).
- **Fix:** upgrade and run again.
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/lib/brokerages/generic.py` — `A futures price keeps its sign`; `src/taxjson/lib/core.py` — `receives_on_buy`

### `tjs run`: generic importer "is mapped to sell but the quantity is positive and the amount negative" on a futures sale at a negative price
- **Check:** the refused row is a futures symbol (`/…`, `F:…`) with a negative price, in an export whose quantities are unsigned.
- **Cause:** the sign check took the sale's cash-out amount for a buy; a negative-price futures sale really pays cash.
- **Fix:** upgrade (or, meanwhile, give the sale a negative quantity).
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/brokerages/generic.py` — `_check_trade_shape`, `trade_amounts`

## Missing purchase history and transfers

### "Error: taxable account margin: its input holds 1 TRANSFER row(s). TRANSFER is not allowed in taxable accounts — replace each with the actual buy/sell history …" after `tjs check-tt` said "Every line parses."
- **Check:** `tjs check-tt` names each `.tt` line it comes from: "inputs/margin/case.tt:1: a TRANSFER line in taxable account margin (type = "taxable"): `tjs run` stops on it" (or "an ACQUIRED line books a counter-TRANSFER …"). The rows listed under the run's error carry the date and symbol.
- **Cause:** a taxable account's books may hold no TRANSFER row the run cannot net: shares moved in or out need their purchase or sale. A `.tt` `TRANSFER` line, or the counter-TRANSFER an `ACQUIRED` line books, stays in a taxable account's books (the broker's own transfer rows are kept aside while `transfers = false`). A zero-net pair of lines, or a line an `OPENING` snapshot of the symbol replaces, is netted and fine. Earlier releases of `tjs check-tt` read every line alone and never saw the account's type. With `transfers = true` on a taxable account, a line may cancel a TRANSFER row of the exports: check-tt says so as a warning, since it does not read the exports.
- **Fix:** replace the line with the purchase as a `BUYSELL` line dated its true day and at its cost (`tjs find-missing-history --write-purchases` drafts it for a transfer-in); for an `ACQUIRED` line, its first half is that `BUYSELL`. A line in a sheltered account (`type = "sheltered"`) is booked as written.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/check_tt.py` — `_taxable_transfers`, `TRANSFER is not allowed in taxable accounts`; `src/taxjson/lib/pipeline.py` — `_handle_transfers`, `_drop_self_cancelling_transfers`, `TransferValidationError`; `src/taxjson/lib/opening.py` — `apply_opening_cutoff`

### "Warning: NOT in the totals: 2 sales with no purchase (QZQ.TO, QZR.US) — `taxjson find-missing-history`" (or "Info: 1 position(s) go short in margin's data (QZQ.TO) — `taxjson find-missing-history`")
- **Check:** the first is the closing block under "Before you trust these numbers"; `tjs run --details` words it "2 positions sold in 2025 with no purchase in your files, not in missing_history.tt: QZQ.TO (margin), QZR.US (margin)". A narrow terminal drops the symbols (and spells the command `tjs`). `tjs find-missing-history` lists each pair under "AFFECTS 2025" with its first negative date and the sales it touches.
- **Cause:** the exports start after the shares were bought (or the shares were transferred in), so the sale has nothing to close. It is booked as a short and its gain is in no total.
- **Fix:** in this order: add an older export that holds the purchase to `inputs/<account>/`; or enter the purchase as a `.tt` BUYSELL line with its real date and cost (`tjs find-missing-history --write-purchases` drafts these from IB's Basis or a transfer's stated book value); only when the history cannot be recovered, `tjs find-missing-history --write-missing-history` writes `OPENING <date> <SYMBOL> <qty> cost=unknown` lines into `inputs/<account>/missing_history.tt`, and those sales must then be reported by hand. docs/getting-started.md step 5 walks through it.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/first_run.py` — `render`, `with no purchase in your files, not in `; `src/taxjson/bin/taxjson_run.py` — `_short_positions_note`; `src/taxjson/lib/missing_history.py` — `detect_missing_history`

### "Info: 1 position(s) go short in margin's data (QZD.TO)" or "Warning: NOT in the totals: 1 sale with no purchase (QZD.TO)" (`--details`: "1 position sold in 2025 with no purchase in your files, not in missing_history.tt: QZD.TO (margin)") after a Norbert's gambit, with a ticker.map `TOBASE` line (or none) instead of `JOURNAL`
- **Check:** `tjs find-missing-history` lists the CAD line (QZD.TO) short on the gambit's day, and the export shows a buy of one line and a sale of the other that day (RBC trade rows ending `CA JNL`). Look for the journal's evidence: its two transfer legs (RBC `TFR - … TRANSFER TO U$ J~1` / `… FROM C$ J~1`, Questrade's BRW `JOURNAL POSITION` pair) dated the settlement day in `tjs transfers`, or a `.tt` `JOURNAL` line. `tjs sum` has the gambit's gain in its totals.
- **Cause:** the missing-history walks read a day's rows by the broker's clock (RBC numbers a day's rows, newest first), so the sale came before the buy. On a journal's days they read buys first: the journal's legs' own days and, in that account within 5 business days of them, the one nearest day whose trades are the journal's own (its units of the FROM listing bought, the same units of the TO listing sold) — for a journal the books show (a join of the run, a Questrade pair id, RBC's J~ reference on an RBC export, a `.tt` `JOURNAL` line); a legacy ticker.map `JOURNAL` line names every day of a buy of one listing and a sale of the same quantity of the other. Opposite trades of another quantity or direction near a journal are not its own: earlier, any such day within 5 business days of any journal between the two listings was read as one, which hid a sale with no purchase (a 100-unit sale of one listing and a buy of the other two days before an unrelated 50-unit journal). A `TOBASE` line names no journal: it says the two listings are one security, not that units moved between them — read as a journal on every day of its symbols it hid a genuinely missing purchase (a sale of one listing with no purchase and a buy of the other; or, after a gambit, any later sale and rebuy). So a gambit with no legs in the export (a `.tt` project, or a `JOURNAL` line `tjs format-map --write` migrated to `TOBASE`) reads as a short until the journal is declared.
- **Fix:** with the broker's legs in the export nothing is needed. Without them, add the journal as a dated event, the line `tjs run --details` gives under "Info: 1 day(s) with a buy of one listing and a sale of the other that a ticker.map TOBASE line joins", in a `.tt` file of the account (`JOURNAL 2025-05-05 QZD.TO QZD.U.TO 1000`), then `tjs run`. If the units were not journaled, the purchase is missing (the entry above).
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/missing_history.py` — `walk_journal_symbols`, `JournalDays`, `_journal_trade_day`, `_opposite_trades`, `_journal_line_symbols`, `_day_trades`, `detected_journal_symbols`, `journal_leg_key`, `_walk_key`, `undeclared_journal_days`; `src/taxjson/bin/taxjson_run.py` — `_report_short_positions`

### "Info: 1 day(s) with a buy of one listing and a sale of the other that a ticker.map TOBASE line joins, the same quantity, and no journal"
- **Check:** the detail (`tjs run --details`) names each day as the `.tt` line to add (`` `JOURNAL 2025-03-03 QZF.TO QZF.U.TO 100` (margin) ``); `tjs find-missing-history` lists the joined symbol short on that day, and `tjs transfers` shows no journal legs for it.
- **Cause:** a buy of one listing and a sale of the same quantity of the other on one day is what a Norbert's gambit looks like, and also what selling shares bought before your files start and buying the other listing looks like. A `TOBASE` line joins the two listings as one security but is no evidence that units moved, so the day reads in the broker's clock order and a sale stamped before the buy has no purchase.
- **Fix:** if the units were journaled, add the named line to a `.tt` file of that account and `tjs run`: the day then reads as the journal's. Otherwise supply the missing purchase (`tjs find-missing-history`).
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/missing_history.py` — `undeclared_journal_days`; `src/taxjson/bin/taxjson_run.py` — `_report_short_positions`, `that a ticker.map TOBASE line `

### "Warning: NOT in the totals: 1 sale with no purchase (QZQ.US)" (`--details`: "… with no purchase in your files … Those sales are NOT in `taxjson sum`") while `tjs sum` has the sale in its totals; or "`taxjson sum` books those sales as short sales closed by a later purchase" or "Info: 1 position read short in 2025 by `taxjson find-missing-history`" (the default view's one-line forms: "Warning: 1 sale with no purchase booked as shorts covered later (QZQ.US)", "Info: 1 position read short by the history check is in the totals (QZQ.US)")
- **Check:** `tjs sum --json`: `no_purchase_uncovered` lists the sales the totals lack; `no_purchase_in_totals` lists those the gains engine booked, with `booked` = `short_cover` or `matched`.
- **Cause:** the closing summary and `tjs sum` said "NOT in `taxjson sum`" for every sale the missing-history walk read as sold before bought, also when the gains engine had booked it: as a short sale a later purchase of the year closed (in the totals, at that purchase's cost), or from a purchase it reads first (the walk and the engine order a day's rows differently). Now the claim is made only for sales the gains files lack (an open short at the year's end); a short closed within the year is a Warning saying so, and a sale the engine matched is an Info line.
- **Fix:** for "NOT in `taxjson sum`" and for a short closed by a later purchase when you held the shares before your files start, supply the purchase (`tjs find-missing-history`, docs/getting-started.md step 5). For the Info line, compare the day's trades with the broker's: the totals already have the sale.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/first_run.py` — `engine_booking`, `BOOKED_SHORT_COVER`, `render_blocks`; `src/taxjson/bin/taxjson_run.py` — `_uncovered_sales`, `no_purchase_in_totals`

### "Info: 1 position read short by the history check is in the totals (QZQ.TO)" for a sale of more shares than you held (bought 40, sold 50), while `tjs find-missing-history` says AFFECTS and `tjs list` shows -10
- **Check:** `tjs sum --json`: `no_purchase_in_totals` names the sale with `booked` = `matched`, `readiness` says complete, yet the sale's row in `tjs sum` has only the units you held (40 of 50); `work/<account>_gains_wash.json` has no record for the other 10.
- **Cause:** the check compared the units the walk read short (10) with the units the engine sold from a purchase (40), not with the part of the sale the engine did not: a sale partly sold from a purchase counted as wholly in the totals. The 10 units the engine holds as an open short at the year's end, with their proceeds, are in no total. Now they are "Warning: NOT in the totals" in `tjs run` and `tjs sum`, `readiness` is incomplete, and the Info line is left for a sale the engine really sold wholly from purchases.
- **Fix:** upgrade, then `tjs run`; supply the missing purchase (`tjs find-missing-history`, docs/getting-started.md step 5).
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/lib/first_run.py` — `engine_booking`, `BOOKED_MATCHED`; `src/taxjson/lib/missing_history.py` — `in_year_short_sales`

### "Warning: NOT in the totals: 1 sale with no purchase (QZC241220C00040000.US)" for a written option whose premium is in `tjs sum`'s totals (`option_grant_timing_since` set)
- **Check:** `tjs sum --json` lists the option under `no_purchase_uncovered`, `readiness` is incomplete, yet `tjs sum`'s option rows hold each write's premium. taxjson.toml sets `option_grant_timing_since` to this year or earlier, and IB codes the first write `C` (closing) although the files hold no long contract before it.
- **Cause:** under grant timing (ITA s.49(1)) a write is booked as a gain the day it is written, and the gains file carries it as a grant record with the sale's id. The check that tells a sale in the totals from one in none read only the purchases a sale closed and the covers in the year, so every such write read as an open short with its proceeds in no total. Now a write booked at grant counts as in the totals, said as a short covered later (a Warning) and listed under `no_purchase_in_totals` with `booked` = `short_cover`. The figures themselves do not change.
- **Fix:** upgrade, then `tjs run`. If the contract was bought before your files start (the `C` code says so), supply that purchase (`tjs find-missing-history`): a write that closed a long position is a sale of it, not a premium.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/first_run.py` — `engine_booking`, `written_by_id`, `BOOKED_SHORT_COVER`

### "Warning: Short position: QZQ.US (margin): its purchase is missing — `taxjson find-missing-history`" (`--details`: "the broker codes the sale on 2025-04-01 CLOSING (IB code C, IB Basis …), but the data holds no position to close")
- **Check:** `tjs find-missing-history` shows the pair with "broker says closing (IB code C)" and IB's Basis for the shares sold.
- **Cause:** IB marks the sale as closing a position, so it is not a short sale: the purchase predates the statements in `inputs/`.
- **Fix:** add the older statement, or `tjs find-missing-history --write-purchases` to draft the purchase line from IB's Basis; fill in the real purchase date and check the cost (IB's Basis is FIFO over IB's lots, not your ACB).
- **Fixed in:** —
- **Code:** `src/taxjson/lib/pipeline.py` — `broker_says_closing`, `CLOSING (IB code C`

### `tjs find-missing-history --write-purchases` drafts `BUYSELL  YYYY-MM-DD  09:30:00  QZK.TO  40  CAD  0.000000  0.00  0` for a transfer-in stated at 0 (`tjs transfers`: `NO_COST_(0_stated)`); with only its date filled in, the sale is filed at a $0 cost
- **Check:** the draft's notes for the line name the transfer row and "the delivering broker's book value 0.00"; on a fixed release they say "the row states a book value of 0" and the line reads `QZK.TO  40  CAD  COST  COST  0`. A `.tt` line kept from an older draft holds `0.000000  0.00`: `tjs transfers` then shows the row `.tt_covers`, `tjs list` the position at `0.00`, and the run warns "positions at a $0 cost".
- **Cause:** the drafts took the book value stated on the row as the cost, 0 included. A 0 there is the delivering broker's placeholder for a cost it was not given, which the run never books (the `NO_COST_(0_stated)` entry above); a draft renamed to `.tt` with only its date filled in booked it as a $0 lot, so the whole proceeds of a sale were its gain.
- **Fix:** upgrade, then `tjs find-missing-history --write-purchases --force`: the line leaves the cost as `COST`, and the run refuses it until you fill it in (0 only if your cost really is nil). A line already renamed to `.tt` at `0.00`: put in the real cost (the sending broker's records, the purchase confirmation), then `tjs run`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/missing_history.py` — `_draft_transfers`, `the row states a book value of 0`, `COST_PLACEHOLDER`

### `tjs find-missing-history --write-purchases`: "QZL.TO 2024-06-03 20 units: your files already acquire it and no sale goes short — a line would count it twice" for a transfer-in `tjs transfers` shows `PARTLY_covered`
- **Check:** `tjs transfers` shows the row `PARTLY_covered` and the run "only PARTLY covered by .tt purchases, NOT booked (QZL.TO)": the account's `.tt` purchases of the stock dated on or before the transfer hold fewer units than arrived.
- **Cause:** the drafts counted the `.tt` purchases as the shares that arrived and drafted nothing, while the run books none of a partly covered arrival and asks for the purchase of the other units.
- **Fix:** upgrade, then `tjs find-missing-history --write-purchases --force`: the draft has a line for the other units (20 arrived, 10 on `.tt` lines: a line of 10) with the date and the cost as `YYYY-MM-DD` and `COST`, and a CHECK note: if the `.tt` lines are part of the transferred units, the other units cost the book value less what those lines paid (or use the purchase confirmation); if they are another holding of the stock in this account, make the line the arrival's whole quantity at the book value.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/missing_history.py` — `_draft_transfers`, `already hold `

### `tjs find-missing-history --write-purchases` drafts two lines from IB's Basis (two sales coded C) where one is missing: filled in, `tjs list` holds shares the broker does not
- **Check:** `tjs find-missing-history` gives the pair's deepest short (`-10.0000`); the draft has a line for each sale coded closing (10 and 10), and between the two sales the files buy the units the second one sells. With both lines in a `.tt` file, `tjs list` holds 10 more than the broker does (`tjs sanity` against the broker's holdings shows the difference).
- **Cause:** the drafts walked the account's rows without the lines they had just drafted, so after the first sale the position still read short and a later sale of a purchase in the files was drafted as a second missing purchase. Now each drafted line counts as held from before the data: a sale goes short only for the units the files and the lines so far do not hold.
- **Fix:** upgrade, then `tjs find-missing-history --write-purchases --force`. A `.tt` file kept from an older draft: remove the extra line (the one for the sale of shares the files bought) and `tjs run`; `tjs sanity` compares the result with the broker's holdings.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/missing_history.py` — `draft_purchases`, `'virt'`

### `tjs find-missing-history --write-purchases`: "a move from your account margin" (not drafted) for an arrival in another account 11 to 45 days after a transfer-out of the same quantity to a registered account
- **Check:** `tjs transfers` shows the out row of the taxable account as an in-kind move (`in-kind_contribution`, or "possibly in-kind … NOT booked" in the run) and the arrival in the other account as `NO_COST` or `book_value`; the run itself books or keeps out the arrival as one from outside your books.
- **Cause:** the drafts paired a slower own move (11 to 45 days apart) as the run does, but without the run's rule that a leg of an in-kind move is no own move: the shares that went to the plan read as the source of the arrival, so no purchase line was drafted. The books were not affected.
- **Fix:** upgrade, then `tjs find-missing-history --write-purchases --force`: the arrival is drafted like any other transfer-in from outside your books.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/missing_history.py` — `_draft_transfers`, `_in_kind`, `in_kind_legs`; `src/taxjson/bin/taxjson_missing_history.py` — `in_kind_taxable_legs`

### "Warning: Short position: QZQ.TO (tfsa): its purchase is missing — `taxjson find-missing-history`" in a registered or crypto account (`--details`: "a registered account (TFSA/RRSP) cannot be short" or "spot crypto cannot be short")
- **Check:** `tjs find-missing-history`; the pair is listed as in a registered account, or the account is a crypto account.
- **Cause:** a registered account or a coin balance cannot go negative, so the books are missing an acquisition (a transfer in, a deposit, a purchase before the exports). Until it is supplied, later purchases cover the phantom short, so a superficial-loss denial they cause is missed; for crypto no gain is booked.
- **Fix:** add the transfer or purchase rows (an older export, or a `.tt` line), or list the pair with `tjs find-missing-history --write-missing-history`. `tjs run --strict` stops on this.
- **Fixed in:** `v0.17.0`
- **Code:** `src/taxjson/lib/pipeline.py` — `spot crypto cannot be short`, `_book_words`; `src/taxjson/bin/taxjson_run.py` — `go short where`

### "Warning: Transfer-in: margin: 1 with NO cost, kept out of the books (QZQ.US) — `taxjson transfers`"
- **Check:** `tjs run --details` words it "1 transfer-in(s) from outside your books have NO cost in the books (30 QZQ.US (2025-05-01))", and the closing block says "Warning: 1 transfer-in kept out with no cost (QZQ.US) — `taxjson transfers`". `tjs transfers` lists the row with IN_BOOKS `NO_COST`.
- **Cause:** shares arrived from another broker and the row states no book value (IB's value column is the market value, never your ACB), so they are kept out of the books and a later sale reads as a short.
- **Fix:** add the original purchase as a `.tt` BUYSELL line (date and ACB from the sending broker) dated on or before the transfer; the warning then stops. docs/getting-started.md step 5c.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/transfer_in.py` — `outside your books have NO cost in the books`; `src/taxjson/bin/taxjson_run.py` — `cmd_transfers_view`

### "Warning: Transfer-in: margin: 1 booked at the broker's stated cost (QZQ.US): check it — `taxjson transfers`"
- **Check:** `tjs run --details` words it "1 transfer-in(s) from outside your books booked at the ACB the broker states on the row". `tjs transfers` shows the row with IN_BOOKS `book_value`.
- **Cause:** a Questrade "TRANSFER BOOK VALUE" or RBC "BOOK VALUE" row is booked as an acquisition on its arrival date at that value. It is the sending broker's record, which is not always your ACB (an earlier superficial-loss denial, a return of capital, the same stock in another account). Tax-logic CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV.
- **Fix:** if the figure is right, nothing; it is said every run. To use your own figure, add the original purchase as a `.tt` BUYSELL dated on or before the transfer; the book value is then no longer used. In a US project the holding period starts at arrival unless you enter the original lots.
- **Fixed in:** `v0.18.0`
- **Code:** `src/taxjson/lib/transfer_in.py` — `the broker `, `states on the row`

### "Warning: Transfer-in: margin: 1 with NO cost, kept out of the books (QZQ.US)" (`--details`: "… the row states a book value of 0, usually the broker's placeholder for the ACB it was not given, never booked as a $0 lot …"); `tjs transfers` shows `NO_COST_(0_stated)`
- **Check:** `tjs transfers`: the row's IN_BOOKS is `NO_COST_(0_stated)`; the export's description carries the broker's book-value wording with 0.00 (Questrade `TRANSFER BOOK VALUE 0.00`, RBC `BOOK VALUE 0.00`).
- **Cause:** the broker states a book value of 0 on the transfer row. A 0 there is usually the broker's placeholder for a cost the sending institution did not report, not a nil ACB, so the run never books the shares as a $0 lot (that would tax the whole proceeds): they are kept out, said as no cost, and a later sale reads as a short (tax-logic CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV). Before this release the line said "the row states no book value" for such a row, as if the broker had been silent (2026-10-24 audit B40).
- **Fix:** add the original purchase as a `.tt` BUYSELL line dated on or before the transfer (date and ACB from the sending broker); if your ACB really is nil (a return of capital brought it to 0), the line's cost is 0. The warning then stops.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/transfer_in.py` — `stated_book_value`, `zero_book_value`, `states a book value of 0`; `src/taxjson/bin/taxjson_run.py` — `0 stated`

### "Warning: Transfer-in: margin: 1 only PARTLY covered by .tt purchases, NOT booked (QZQ.US) — `taxjson transfers`" (`--details`: "… only PARTLY covered by your .tt purchases (20 QZQ.US (2025-03-03, 10 of 20 covered)): NOT booked …")
- **Check:** `tjs transfers` shows the row's IN_BOOKS as `PARTLY_covered` and a `!` line names it; the account's `.tt` BUYSELL lines of the stock dated on or before the transfer add up to fewer units than arrived. The closing block of `tjs run` counts the arrival among the transfer-ins kept out with no cost.
- **Cause:** your `.tt` purchases account for some of the transferred units only. Booking the whole arrival at the broker's book value would hold the covered units twice (before this release it did: 10 `.tt` units and a 20-unit arrival made 30 in the books, 2026-10-24 audit B41); booking the rest pro rata would guess which units the `.tt` lines are. So nothing is booked: the units the `.tt` lines do not account for have no cost and a later sale of them reads as a short (tax-logic CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV).
- **Fix:** add the purchase of the other units as a `.tt` BUYSELL line dated on or before the transfer (date and ACB from the sending broker; the book value the broker states on the row is a start for the figure). If the `.tt` purchases are another holding of the stock in this account (bought there, not the shares that arrived), add the arrival's whole quantity instead. The line stops once the `.tt` purchases cover the arrival.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/transfer_in.py` — `mark_covered`, `covered_units`, `partly_covered`, `only PARTLY covered`; `src/taxjson/lib/stage_msg.py` — `only PARTLY`; `src/taxjson/bin/taxjson_run.py` — `PARTLY covered`; `src/taxjson/lib/first_run.py` — `partly_covered`

### "Warning: Transfer-in: margin: 1 booked at the broker's stated cost (QZQ.US)" (or "… with NO cost …", "… only PARTLY covered …") although a `.tt` BUYSELL dated before the transfer covers it: the purchase is pre-split, or a sale is dated the transfer day
- **Check:** `tjs transfers` shows `book_value`, `NO_COST` or `PARTLY_covered` for the row while `inputs/<account>/*.tt` holds the purchase. Either a `.tt` `SPLIT` line (or the split itself) lies between the purchase and the transfer (bought 10, 2-for-1, 20 arrived), or a `.tt` sale of the stock is dated the transfer day.
- **Cause:** the coverage check read the `.tt` purchases and sales by date only: it skipped `SPLIT` lines, so a pre-split purchase covered only its pre-split units (2026-10-24 audit B04), and it subtracted a sale dated the transfer day even when the sale came later that day — shares that arrived at 10:00 and were sold at 15:00 (B42). Now the account's `.tt` `SPLIT` lines before the transfer are replayed (a rename on the line moves the units), and a same-day sale consumes only when its time is earlier than the transfer row's.
- **Fix:** upgrade, then `tjs run`. A split that happened at the sending broker is not in your export: add it as a `.tt` line (`SPLIT 2024-06-03 09:30:00 QZQ.US QZQ.US 2`) beside the pre-split purchase, or enter the purchase in post-split units.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/transfer_in.py` — `_tt_events`, `_tt_units_at`, `mark_covered`

### Canada: after a move of your own between two taxable accounts, `tjs list` shows the receiving account short ("missing history?") and `tjs find-missing-history` says "AFFECTS 2024 - missing basis distorts this year's gain; fix before filing" for a sale whose cost is right
- **Check:** `tjs transfers`: the out row in one taxable account and the in row in another, the in row's IN_BOOKS `own_move`. `tjs list` shows the sending account still holding the moved shares and the receiving one short by them; `tjs sanity` a `QTY_MISMATCH` (sender) and a `MISSING_IN_HOLDINGS` (receiver); the run says "1 position(s) go short in qb's data"; the checklist's steps 10 and 22 need attention. `tjs sum` is right: the sale's cost is the pool's.
- **Cause:** the ACB is one pool across your taxable accounts (s.47), so the run moved nothing for a move of your own: the units stayed in the sending account's books, and every per-account view read the receiving account's sale of them as a sale with no purchase. A US project's books already carried the move.
- **Fix:** upgrade, then `tjs run`: each account's books carry the move as two lot-move legs (work/<account>_own_moves.json), so the views show the receiving account holding the shares. No figure you file changes: the pool, Schedule 3 and the superficial-loss result are as before, and a filed year's lock still checks OK (`tjs check-filed`). Add no `.tt` purchase or missing-history line for such a sale; an `OPENING … cost=unknown` line written for one now draws "ATTENTION: … lists ZZQ.TO / qb, but its rows never go short any more": delete it.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/transfer_in.py` — `OwnMove`, `arrivals`; `src/taxjson/bin/taxjson_run.py` — `stage_transfer_arrivals`, `_stage_pooled_own_moves`, `_move_legs`; `src/taxjson/lib/core.py` — `_own_move_view`, `_own_move_units`, `LOT_MOVE_TYPE`; `src/taxjson/lib/corporate_timeline.py` — `_walk_rest`; `src/taxjson/lib/export_coverage.py` — `account_gaps`

### `tjs transfers` shows `own_move` (nothing booked) for a transfer-in that came from outside your books: a transfer-out of another stock the same day, or of the same stock years later
- **Check:** `tjs transfers`: the in row's IN_BOOKS is `own_move`, and the only transfer-out that could pair with it is another security's (`QZQ.US` out, `QZR.US` in, the same quantity within 3 days) or the same security's long before or after (a 2020 arrival, a 2026 departure). The arrival's shares are then in no account's books: `tjs sanity` shows the position missing, or a sale of them reads as a short.
- **Cause:** a transfer-out cancelled a transfer-in of the same quantity in one account within 3 days whatever the two securities (2026-10-24 audit B03), and a transfer-out of the same security cancelled a transfer-in of it however far apart (the closest date first, with no limit: B50). Both read an arrival from outside the books as a move of your own, so no acquisition was booked. Now a listing journal with no pair id and no ticker.map rule cancels only two listings of one root (`QZQ.TO` / `QZQ.US`), and a transfer-out pairs with a transfer-in of the same security only within 10 days (the window the in-kind pairing allows a transfer between two brokers, `lib/in_kind.PAIR_DAYS`), or 11 to 45 days before it when the two are the same quantity in two of your accounts and neither has another candidate (a slower move of your own: the next entry); other legs stay an arrival and a departure of their own, said as ATTENTION (tax-logic CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV). A departure after the arrival is never its source.
- **Fix:** upgrade, then `tjs run`: the arrival is booked at the broker's stated book value or listed as `NO_COST` (add the purchase, docs/getting-started.md step 5c). If the two legs really are one journal between two listings whose tickers differ, join them with a ticker.map `TOBASE` line (`tjs ticker-map --suggest` drafts it): the legs then cancel as one security. A move of your own whose legs post more than 10 days apart: the next entry.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/transfer_in.py` — `arrivals`, `OWN_MOVE_DAYS`, `LISTING_JOURNAL_DAYS`; `src/taxjson/lib/cross_listings.py` — `listing_root`

### A move of your own between two taxable accounts whose legs post more than 10 days apart: "Warning: Transfer-in: qb: 1 booked at the broker's stated cost (QZQ.TO)", `tjs list` still shows the shares in the sending account and the sale's cost is blended
- **Check:** `tjs transfers`: the out row in one account and the in row in another (IN_BOOKS `book_value` or `NO_COST`), the same stock and quantity, the in row 11 to 45 days after the out row (an ATON move often takes one to three weeks); `tjs list` shows the sending account still holding the shares the broker no longer holds, and `tjs sum`'s cost averages the purchase with the book value on the in row. On a fixed release the run says `Info: Own-account move: QZQ.TO 100 qa -> qb (out 2024-03-01, in 2024-03-16: 15 days apart, nothing else pairs)` (a US project: `… not a sale — the lots' basis and purchase dates go to qb.`); when the legs do not pair, the arrival's warning says `Warning: Transfer-in: qb: 1 booked at the broker's stated cost (QZQ.TO): maybe a move of your own` and its detail (`tjs run --details`) "It may be the other end of a transfer-out of yours that nothing paired (…)".
- **Cause:** a transfer-out of a taxable account never leaves the books (the shares' cost comes from their purchase), so the transfer-in of a move of your own must cancel against it. Since the 2026-10-24 audit's B50 fix the two legs cancelled only within 10 days, and a slower move was read as an arrival from outside your books: booked a second time at the broker's book value (the pool held twice the units at a blended ACB, the filed gain changed) or kept out with no cost, while nothing named the departure (2026-10-25 audit diff-engine#4). Now an out leg and an in leg of the same security and quantity in two of your taxable accounts, the in leg 11 to 45 days after the out leg, are one move of your own when neither has another candidate: nothing is booked for the arrival, the shares keep their ACB, the run says it as Info with both dates, and a US project carries the sending account's lots to the receiving one (tax-logic CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV / US-BASIS-05). Legs that could pair otherwise (two departures for one arrival, two arrivals for one departure, one back in the sending account), a departure partly paired already or of another quantity, legs in one account, a leg of an in-kind move (to or from a registered account, booked or not) and legs more than 45 days apart pair nothing; the arrival is booked (or kept out) as before, and its ATTENTION line names a departure nothing paired up to 45 days before it. A departure after the arrival is never its source (B50).
- **Fix:** upgrade, then `tjs run`: the Info line names the move and the sale's cost is the purchase's. When the ATTENTION line names a departure instead, the shares are held twice if they are one move (the sending account's books keep them): `tjs sanity` compares each account with the broker's holdings, and `tjs transfers` lists both rows; do not add a `.tt` purchase for such an arrival (it would hold them twice as well). Two legs that are not one move although they pair (the shares left your books, other shares arrived): a `.tt` `NOTOWN` line undoes the pairing (the next entry).
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/transfer_in.py` — `slow_pairs`, `SLOW_MOVE_DAYS`, `SlowMove`, `slow_dates`, `departures`, `nothing paired`; `src/taxjson/bin/taxjson_run.py` — `stage_transfer_arrivals`, `transfer_arrivals`, `in_kind_leg_idents`, `_custody_pairing`, `stage_own_account_moves`; `src/taxjson/lib/stage_msg.py` — `_maybe_own`; `src/taxjson/lib/missing_history.py` — `_draft_transfers`

### "Info: Own-account move: QZQ.TO 100 qa -> qb (out 2024-03-01, in 2024-03-16: 15 days apart, nothing else pairs)" (or `tjs transfers` shows `own_move`) for two rows that are not one move
- **Check:** `tjs transfers`: qa's transfer-out and qb's transfer-in of the same stock and quantity, qb's IN_BOOKS `own_move` (a pairing within 10 days says no Info line; `own_move` alone). They are not one move when the shares that left qa did not go to qb (given away, moved to an account outside the project) and qb's came from outside your books (another broker): the run gave qb's shares qa's ACB (US: qa's lots and purchase dates) and did not use the book value the broker states on qb's row. `tjs run --details` shows the Info line's detail, the line that undoes the pairing.
- **Cause:** a transfer-out and a transfer-in of the same security and quantity in your taxable accounts within 10 days of each other, or 11 to 45 days apart when neither has another candidate, are paired as one move of your own: the rows say nothing more, so an unrelated pair like that pairs as well (docs/limits.md "Transfers into a taxable account"). Nothing let you undo it (backlog TJ-0075).
- **Fix:** upgrade, then add the line the detail prints to a `.tt` file in the receiving account's folder (e.g. `inputs/qb/transfers.tt`): `NOTOWN 2024-03-16 QZQ.TO 100 qa 2024-03-01` (the transfer-in's date, symbol and units, then the transfer-out's account and date, as `tjs transfers` lists them), and `tjs run`. The transfer-in is then booked as one from outside your books (at the book value on its row: `Warning: Transfer-in: qb: 1 booked at the broker's stated cost`; else with NO cost: add its purchase, docs/getting-started.md step 5c), and the transfer-out is an ordinary one: qa's books keep its shares, as for any transfer-out (`tjs sanity` compares them with the broker's holdings). The figures change only with the line; every other transfer pairs as before. The line never makes a pairing, and no line forces one. A line that undoes nothing says so (the next entry).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/transfer_in.py` — `NotOwn`, `parse_not_own_line`, `read_not_own`, `not_own_text`, `arrivals`; `src/taxjson/bin/taxjson_run.py` — `stage_transfer_arrivals`, `transfer_arrivals`, `not_own_legs`, `_custody_pairing`; `src/taxjson/lib/missing_history.py` — `_draft_transfers`

### "Warning: inputs/qb/transfers.tt:1: this NOTOWN line undoes no pairing, so nothing changed"
- **Check:** `tjs run --details` shows the line and why. `tjs transfers` lists the two rows it should name: the transfer-in's date in this account, its symbol and units, the transfer-out's account and date, and the transfer-in's IN_BOOKS `own_move`.
- **Cause:** the line names no pairing the run made: another date, quantity or account (a typo), a pairing of two different quantities (a delivery in parts), a listing journal, two rows the run does not pair (the transfer-in already says `book_value` or `NO_COST`), or a line in an account whose transfer rows are never paired (sheltered, crypto, `transfers = true`). The books are as without the line.
- **Fix:** write the line as `NOTOWN <date> <symbol> <qty> <from-account> <from-date>` with the two rows' values (`tjs check-tt` also names a line in such an account, or one naming an account taxjson.toml does not declare), or delete it.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `stage_transfer_arrivals`, `undoes no pairing`; `src/taxjson/lib/transfer_in.py` — `not_own_account_problem`; `src/taxjson/lib/check_tt.py` — `_not_own_accounts`

### "Warning: 1 transfer in a taxable loss's 30-day window counted as an account move, not a purchase"
- **Check:** the detail lines (`tjs run --details`) name each transfer, its date and the loss sale; `tjs transfers` lists the rows.
- **Cause:** a transfer between your accounts moves shares; it is not an acquisition, so it does not deny the loss (CA-SL-16 / US-WASH-23). If one leg was really a contribution to a registered plan or a purchase, the loss may be superficial (or a wash sale).
- **Fix:** if it was an account move, nothing. A contribution in kind from one of your taxable accounts in the project is not listed here: the run pairs it with the taxable account's transfer-out, books the sale at fair market value and counts the plan's purchase (the "in-kind move(s) … booked" warning; CA-INKIND-04). If it was a contribution from outside the project or a purchase, record it as a BUYSELL dated the day it was acquired (or, for a transfer-out of your taxable account whose plan is not in the project, an `INKIND` line with `plan=`); `[settings] transfers_as_acquisitions = true` counts every transfer.
- **Fixed in:** `v0.22.0`
- **Code:** `src/taxjson/lib/pipeline.py` — `transfer_window_message`, `transfers_as_acquisitions`; `src/taxjson/bin/taxjson_run.py` — `_say_transfer_windows`, `stage_in_kind_context`

### "Warning: 1 in-kind move(s) between your taxable and registered accounts booked at fair market value" (or "… NOT booked", "… NOT booked — ambiguous")
- **Check:** each detail line (`tjs run --details`) names the move (`contribution margin → rrsp: 100 QZQ.TO on 2025-03-14`), its value and where the value came from, and the gain or the loss denied; `tjs transfers` labels both rows `in-kind_contribution` / `in-kind_withdrawal` (an ambiguous pair: "ambiguous, NOT booked").
- **Cause:** a taxable account's transfer-out and a registered account's transfer-in of the same security and quantity within 10 days (or the reverse) are an in-kind contribution (withdrawal), not a move of your own. Canada books a contribution as a sale at fair market value (a loss is nil for good, s.40(2)(g)(iv), shown apart from DENIED) and a withdrawal as a purchase at that value (CA-INKIND-01 … CA-INKIND-06). A US contribution is NOT booked: an IRA takes cash only (US-INKIND-01). A move is NOT booked either when no value was found (no `INKIND` line, no market value on the transfer rows, no close from Yahoo), or when it is ambiguous: legs of the same kind (taxable with taxable, registered with registered) pair first across the project as moves of your own, and a taxable leg with a registered one is booked only when neither has another leg of the same security and quantity in the window that could be its partner. Before this, a closer registered leg won over a taxable one, so two moves of your own (one taxable, one registered) could be booked as a contribution and a withdrawal.
- **Fix:** check each value; a value marked ESTIMATED is Yahoo's split-adjusted close. To set your own, add the line the warning prints (`tjs run --details`) to a `.tt` file in the taxable account's folder (`INKIND 2025-03-14 QZQ.TO -100 CAD 15.00`; negative = shares out to the plan). For an ambiguous pair, the detail line names the other candidates and both answers: the `INKIND` line with `plan=` books the move (in the folder of the taxable account whose move it was), `INKIND 2025-03-14 QZQ.TO -100 plan=own` keeps that transfer row a move of your own. A US contribution in kind is usually a mistake in the rows (or a rollover between retirement accounts): check `tjs transfers`. `tjs run --strict` stops while a move is not booked.
- **Fixed in:** `v0.23.0`
- **Code:** `src/taxjson/lib/in_kind.py` — `message`, `NOT booked`, `ambiguous`, `pair_all`, `line_legs`, `value`; `src/taxjson/bin/taxjson_run.py` — `_say_in_kind`, `in_kind_state`

### `tjs sum` has no `!` line and `tjs checklist` marks transfers done, though `tjs run` said "in-kind move(s) between your taxable and registered accounts NOT booked"
- **Check:** `tjs transfers` labels both rows `in-kind_contribution_(NOT_booked)` and `tjs list` still holds the contributed shares in the taxable account. Since the fix `tjs sum` prints "! 1 in-kind move with a registered account NOT booked: NOT in these totals (ZZB.TO) — tjs transfers" (`--details`: the `INKIND` line to add), `sum --json` readiness has an `in-kind-not-booked` issue, and the checklist's transfers step needs attention.
- **Cause:** a move the run could not value (no `INKIND` line, no market value on the transfer rows, no close) is not booked: a contribution's deemed sale (a withdrawal's purchase) is missing from the books, and only the run's own warning said so.
- **Fix:** add the `INKIND` line with the fair market value per share to a `.tt` file in the taxable account's folder (`INKIND 2024-05-01 ZZB.TO -50 CAD 12.00`), then `tjs run`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `in_kind_unbooked`, `in_kind_fix_line`, `cmd_summary`; `src/taxjson/lib/readiness.py` — `in-kind-not-booked`; `src/taxjson/lib/checklist.py` — `s_transfers`

### "Warning: 1 transfer row(s) of a taxable account possibly in-kind in parts with a registered account — NOT booked"
- **Check:** each detail line (`tjs run --details`) names the taxable transfer row (`margin: 100 QZQ.TO out on 2025-03-14`) and the registered account's rows of the same security the other way in the window (`rrsp received 60 on 2025-03-17, 40 on 2025-03-17`); `tjs transfers` lists them.
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

### "warning: price-chain: the close of QZL.L (QZL.L) on 2025-02-03 did not say its unit — that market quotes in pence/cents as well as pounds, so it was not used", and the in-kind move is "NOT booked — no market value on the transfer rows and no close found"
- **Check:** the security is an LSE (`.L`), JSE (`.JO`) or Tel Aviv (`.TA`) line, or a ticker.map `QUOTE` line spells it as one. A variant names the unit instead: "currency 'USd' looks like a minor unit (pence/cents) taxjson does not know".
- **Cause:** those markets quote in pence, cents or agorot. Yahoo names the unit (`GBp`, `ZAc`, `ILA`) and taxjson converts the close to pounds, rand or shekels (CA-INKIND-06 / US-INKIND-03); a close that names no unit, or a unit taxjson has no table entry for, could be 100 times off, so it is not used. An earlier release read the unit off the `.L` suffix and valued a 125p close as 125 pounds, booking the move at 100 times its value; the closes it cached for these markets are not trusted and are looked up again.
- **Fix:** run once online (without TAXJSON_OFFLINE): Yahoo names the unit. Or add the value per share in the major currency as a line in a `.tt` file in the taxable account's folder: `INKIND 2025-02-03 QZL.L -100 GBP 1.25`. If an earlier release booked such a move, re-run with this one and compare.
- **Fixed in:** `v0.29.1`
- **Code:** `src/taxjson/lib/price_chain.py` — `close_on`, `provider_unit`, `_yf_history_fetcher`, `did not say its unit`

### "warning: price-chain: the close cache entry for QZQ.US (QZQ) on 2025-02-03 is dated 2025-12-31, outside 2025-01-27..2025-02-03 — not used", then the close is looked up again (offline: the in-kind move "cannot be valued: TAXJSON_OFFLINE is set and the close cache has no price for the date")
- **Check:** in `work/.close_cache.json` the entry under `QZQ.US|QZQ@2025-02-03` has a `day` after the asked day, more than 7 days before it, or not a date at all: the file was edited by hand or damaged. On an older release the move was valued at that entry's price with no word.
- **Cause:** the close cache took an entry's `day` on trust, so a December close filed under a January key was served as the January value (a price from the future). An entry's day must now be one the lookup could have returned for that key (on or before the asked day, within the 7-day lookback); any other entry is a miss.
- **Fix:** run once online (without TAXJSON_OFFLINE): the close is looked up again and the entry rewritten. Or add the `INKIND` line with the value per share (the entry above).
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/price_chain.py` — `close_on`, `_iso_day_in`, `CLOSE_LOOKBACK_DAYS`, `outside`

### An in-kind move of an option contract whose size is not 100 (an adjusted series, a mini) is booked at twice (ten times) its value: `tjs transfers` shows the move "at 200.00 USD (Yahoo close …, ESTIMATED)" for one contract quoted at 2 whose multiplier is 50
- **Check:** the transfer row's `multiplier` (IB's Transfers state the contract size; `tjs events all ACCOUNT` shows the row) is not 100, and the move's value is the close × 100 per contract; the booked row's price is the value ÷ 100.
- **Cause:** the valuation from a Yahoo close and the booked row's price took 100 units per contract for every option, whatever size the row declared (audit 2026-10-24 B20). A value from an `INKIND` line or from the broker's market value on the row was right.
- **Fix:** upgrade: the move is valued and priced at the row's declared size, else a ticker.map `MULT` line, else 100 (lib/core's rule, said once per root when it is assumed), and the booked row carries that `multiplier`. Then `tjs run`. On an older release, add the value as a line: `INKIND 2026-01-02 ZZQ270115C00010000.US -1 USD 100` (the whole value, in the taxable account's folder).
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/in_kind.py` — `contract_size`, `value`, `booked_rows`

### A declared in-kind move delivered in parts (an `INKIND` line, the plan receiving the shares in several transfer rows) is booked with one set of the plan's legs although another set of its legs adds up to the quantity too; on this release the same line is "possibly in-kind in parts — NOT booked" with the plan's legs listed
- **Check:** `tjs transfers` lists more than eight transfer-in rows of the security in the plan within 10 days of the taxable account's transfer-out, and two different sets of them add up to its quantity.
- **Cause:** the search for the plan's legs that add up to the declared quantity looked at the eight nearest legs only and called the one set it found there unique; a ninth leg that made a second set was never seen (audit 2026-10-24 B43). The move's taxable side was booked with the first set.
- **Fix:** upgrade: every candidate leg counts (within a fixed search budget; past it nothing is proven unique), so two sets leave the move's plan side unmatched: the `INKIND` line still books the taxable side at its value; the plan's legs are listed as possibly in parts. Nothing to do unless the plan's acquisition dates matter (the superficial-loss window, CA-INKIND-04): then split the line into one per part (`INKIND <date> <SYMBOL> -<qty> <CUR> <value>` each, the plan's dates and quantities).
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/in_kind.py` — `_parts`, `_PARTS_STEPS`, `pair_all`

### "Warning: 2 positions at a $0 cost (QZA.US, QZB.TO) — `taxjson find-missing-history`"
- **Check:** `tjs run --details` words it "2 positions at a $0 cost (1 sold in 2025, 1 still held)"; `tjs find-missing-history` lists them under "$0-cost corp-action shares". One lot partly sold and partly held is one position, listed under both (before this release it was counted twice: "2 positions at a $0 cost (QZA.US, QZA.US)", backlog TJ-0044).
- **Cause:** a corporate action (a spin-off, a stock dividend, a merger) put shares in the books at no cost, so their sale overstates the gain.
- **Fix:** give the event its value: `tjs elect ACCOUNT --set EVENT_ID=ELECTION --hint fmv_per_share=<value>` for a spin-off or merger, or a `[[distributions]]` entry / `.tt` ADJUST for a stock dividend. docs/getting-started.md step 5d.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/first_run.py` — `zero_cost_sold`, `zero_cost_held`, `_distinct`; `src/taxjson/lib/missing_history.py` — `detect_missing_history`

### "Warning: 1 position at a $0 cost (ZZS.TO) — `taxjson find-missing-history`" (and find-missing-history "$0-COST CORP-ACTION SHARES LATER SOLD — inflated gain") after the `.tt` BUY of an s.86.1 spin-off's fraction at 0 and its SELL for the cash
- **Check:** you elected `rollover_s_86_1` with an `allocated_acb_cad`, and added the `.tt` pair the UNBOOKED warning asks for (`BUYSELL … ZZS.TO 0.5 CAD 0 0` and its sale); `tjs gains` gives the fraction's sale a cost (its share of the pooled allocation), not $0.
- **Cause:** the $0-cost check flagged any lot entered at $0 and later sold, though this one joins a pool whose cost the election set, which covers the fraction (backlog TJ-0101).
- **Fix:** upgrade: a `.tt` BUY of less than one share at $0, sold again by a `.tt` line within the days the corporate-action stage accepts (from the spin-off to 50 days after the cash was paid), into a pool an s.86.1 election gave a cost, is not flagged. The books were right either way.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/missing_history.py` — `detect_zero_basis_acquisitions`, `_hand_fraction`; `src/taxjson/lib/corp_actions.py` — `_TT_FRACTION_DAYS`

### "Warning: 1 security paid income the books do not hold (QZQ.US) — `taxjson sanity`"
- **Check:** `tjs run --details` words it "1 security paid income in 2025 that the books do not hold (a holding with no purchase in your files?)"; `tjs sanity` (and `tjs divs` for the symbol) shows dividends on a symbol with no position.
- **Cause:** the shares were bought before the exports start, or arrived by transfer, and were never sold this year, so nothing goes short; only the income shows they are held.
- **Fix:** add the purchase history as for a missing purchase (an older export or a `.tt` BUYSELL line), or check the dividend's symbol against `ticker.map`.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/first_run.py` — `income_not_held`, `that the books do not hold`

### "Error: account margin: the opening balance of QZR.TO dated 2025-03-03 would leave out 1 return(s) of capital of the 2025 tax year (first: 2025-02-03 return of capital of 150 CAD on QZR.TO, which may go beyond the cost)"; on an older release the run went on and the gain was in no total
- **Check:** the account has an `OPENING` line dated in the tax year, and its rows of that symbol before the snapshot include a return of capital (an `ADJUST ... type=roc` line or a broker's ROC row) of the tax year. Without the `OPENING` line, `tjs form-export` shows a gain with no proceeds for that symbol; with it, on an older release, the gain is gone.
- **Cause:** a return of capital beyond the cost is a gain of the year it is received (ITA s.40(3); in a US project §301(c)(3)) with no sale behind it. The cut-off left out the account's rows before the snapshot and only checked sales and short covers, so that gain dropped out of the year in both countries. The check now walks the cost back from the snapshot's: a return of capital of the tax year stops the run when no shares were held at it, when the cost right after it is nil or cannot be told (another currency, a transfer or a sale between), or, in a US project, when a snapshot lot bought before it has a nil basis. One the snapshot's cost shows stayed within the cost is history the snapshot replaces. A `[[distributions]]` return of capital dated before the snapshot is not applied and is named in an `ATTENTION: [[distributions]]` line.
- **Fix:** upgrade and `tjs run`. Take the snapshot from a statement before the year's first return of capital of that symbol (December 31 of the year before), or remove the `OPENING` line and supply the history.
- **Fixed in:** `v0.29.1`
- **Code:** `src/taxjson/lib/opening.py` — `_realizations_left_out`, `_nil_lot_held`, `_refusal`; `src/taxjson/bin/taxjson_apply_distributions.py` — `_snapshot_after`

### "Error: account margin: the opening balance of QZS.US dated 2025-01-31 would leave out 1 short cover(s) of the 2025 tax year (first: 2025-01-06 cover of 10 QZS.US: a buy of 10 that closes a short position)"; on an older release the run went on and the cover's gain was in no total
- **Check:** the account has an `OPENING` line (`inputs/<account>/opening_<date>.tt`) dated in the tax year, and its rows of that symbol before the snapshot include a buy that closes a short position (a short sale, or a written option bought back). `tjs sum` on an older release has no disposition for that cover.
- **Cause:** an opening snapshot leaves the account's earlier rows of its symbols out of the books (tax-logic CA-OPEN-03 / US-OPEN-03), and a left-out sale of the tax year stops the run. Only sales (negative quantities) were checked: a cover is a positive-quantity buy, so a cover of the tax year was dropped silently and the short sale's gain or loss fell out of the year in both countries. The check now reads each left-out row's position, walked back from the snapshot's quantity, and stops on a cover too (including a buy that crosses from short to long). The walk undoes each split of the security once, whichever account's rows carry it, and orders a row with no time at 00:00:00, as the engines do.
- **Fix:** upgrade and `tjs run`. Take the snapshot from a statement before the year's first sale or cover of that symbol (December 31 of the year before), or remove the `OPENING` line and supply the history.
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/lib/opening.py` — `apply_opening_cutoff`, `_realizations_left_out`, `_when`

### "ATTENTION: opening: … the opening snapshot (…) replaces N earlier row(s) of NEW" counts a purchase of a reused ticker, then "Warning: 1 position sold … with no purchase in your files"
- **Check:** the account has a rename (SPLIT or RENAME OLD -> NEW), a later BUY under the reused OLD ticker, and an OPENING of NEW dated after both; the OLD sale is left out of `tjs sum`.
- **Cause:** the opening cut-off followed every rename dated on or before the snapshot whatever the row's own date, so a purchase made under the old ticker after its rename was taken for the renamed holding and dropped (2026-10 audit #27).
- **Fix:** upgrade: a row follows only renames dated on or after its own date, so a reused ticker keeps its rows. On an older release, date the OPENING before the reused ticker's first purchase.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/opening.py` — `_final_symbol`, `apply_opening_cutoff`

### `tjs opening` writes an option at a cost 100 times too low ("OPENING … 1 CAD 2.00" for one contract at 2)
- **Check:** the holdings TOML gives the option's cost as `average_entry_price` (the Questrade snapshot `tjs fetch --positions` writes) with no `total_cost`; the sale's gain is too high.
- **Cause:** the TOML reader multiplied the per-share price by the number of contracts only, without the contract size the RBC reader and sanity use (2026-10 audit #28).
- **Fix:** upgrade: `average_entry_price` is multiplied by `multiplier` (or `contract_multiplier`), else 100 for an equity option; `cost_per_share` stays per holding unit, as `taxjson-export` writes it. On an older release, write `total_cost` (price × 100 × contracts) in the TOML.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/positions_reports.py` — `_read_toml`

### `tjs opening` writes a second opening of a symbol already opened in a `.TT` file ("ATTENTION: dedup: X.TT and opening_DATE.tt both hold 1 identical line(s)")
- **Check:** the account folder has a file with an uppercase `.TT` suffix holding an OPENING line for a symbol of the report; `tjs opening` succeeded and `tjs run` booked both openings.
- **Cause:** the clash check looked for `*.tt` case-sensitively, while the run reads `.tt` files in any case (2026-10 audit #30).
- **Fix:** upgrade: the check reads every `.tt` file the run reads and refuses with "… already has OPENING lines for …". On an older release, rename the file to `.tt` first, or remove the duplicate OPENING lines.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_opening`, `input_files`

### `tjs opening` accepts a holdings report of two broker accounts whose ids start with the same two characters (header "accounts U1***")
- **Check:** the report (a holdings TOML `account =` per holding, or an RBC Holdings Export Account column) lists two account ids sharing their first two characters; both accounts' positions went into one taxjson account.
- **Cause:** account ids were masked (two characters + ***) when the report was read, and the one-account guard counted the masked labels (2026-10 audit #48).
- **Fix:** upgrade: the guard counts a key of the whole id and refuses with "lists 2 broker accounts (U1***, U1*** — ids that differ after the two characters shown)"; output still shows only masked ids. On an older release, split the report into one file per broker account.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/positions_reports.py` — `account_key`, `PositionRow`; `src/taxjson/bin/taxjson_run.py` — `cmd_opening`

### `tjs run`: no missing-export warning although shares remain after a split and a partial sale
- **Check:** a SPLIT (or rename) falls between the broker export's last day and the year end, followed by a sale of only part of the post-split shares.
- **Cause:** the gap check skipped split rows, so the sale looked like a full exit.
- **Fix:** upgrade; the missing export is then flagged. Add the newer export.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/export_coverage.py` — `closed_later`

## Holdings

### "Info: 5 accounts with positions not checked against the broker's holdings — `taxjson sanity`" (`--details`: "5 accounts with open positions and no holdings file to check them against"; or `tjs sanity`: "UNCHECKED: 5 account(s) with positions and no holdings file: … — nothing to compare with")
- **Check:** `tjs checklist --all` shows step `sanity` as `[!]` with the same `UNCHECKED` line (`[m]`, to confirm, on releases before the fix below); `ls holdings/` holds no `.toml`.
- **Cause:** nothing compares the books' positions with the broker's own positions report yet.
- **Fix:** save the broker's positions snapshot (a `[[holding]]` TOML, as a download tool writes it) in the year's `holdings/` folder, named for the account (`margin_holdings.toml`) or carrying its broker account id in `[meta] broker_account` (or `[meta] account`); or add `holdings = ["~/holdings/margin.toml"]` under `[accounts.margin]`. Then `tjs sanity` and `tjs run` check it every time. One-off: `tjs sanity margin=/full/path/positions.toml`.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/first_run.py` — `unchecked_accounts`; `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `nothing to compare with`

### `tjs run` ends with "Info: 3 accounts with positions not checked against the broker's holdings — `taxjson sanity`" although the run's positions check just compared their holdings/ snapshots
- **Check:** `tjs run --details` names the accounts ("… and no holdings file to check them against: margin (8), …"); `tjs sanity` lists a snapshot in `holdings/` for each of them (and `UNCHECKED:` names none of them).
- **Cause:** the closing summary counted an account as checked only when taxjson.toml gave it `holdings = [...]`; a snapshot the holdings check finds in `holdings/` by its broker account id or its file name was compared but not counted.
- **Fix:** upgrade: an account with a `holdings/` snapshot that `tjs sanity` claims for it counts as checked; one with none (a crypto account, say) is still listed. On an older release the line is harmless for such accounts.
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/lib/first_run.py` — `unchecked_accounts`, `collect`; `src/taxjson/lib/holdings_dir.py` — `discover`, `folder_for`

### `tjs sanity`: "Info: holdings/U1***_positions.toml: no account claims it — add its broker account id to the account"
- **Check:** the file's `[meta] broker_account` and `[meta] account` (masked here) are in no account's `account` or `broker_accounts`, its name does not start with an account name, and no account's `holdings = [...]` lists it.
- **Cause:** a snapshot in `holdings/` is matched to its account by the broker account id it states, else by its file name; this one matches neither, so it is not compared. (Before the fix, a file an account lists in its own `holdings = [...]` was named here too, though that account compares it.)
- **Fix:** add the id under its account (`broker_accounts = ["…"]`), rename the file `<account>_holdings.toml`, or list it in the account's `holdings = [...]`. An id two accounts declare is refused the same way.
- **Fixed in:** `v0.26.0`
- **Code:** `src/taxjson/lib/holdings_dir.py` — `discover`, `listed_files`, `claims it`

### `tjs sanity`: "Info: holdings/margin.toml: broker account 99*** is other's, whose own `holdings = [...]` setting names its files — not compared"; on an older release the snapshot was compared with `margin`'s books (MISSING / EXTRA positions that are another account's)
- **Check:** the file's `[meta] broker_account` (masked here) is the `account` or one of the `broker_accounts` of an account that has its own `holdings = [...]` setting, and the file's name starts with another account's name (`margin.toml`, `margin_holdings.toml`).
- **Cause:** a snapshot's owner is the account declaring its broker id, else the account its name starts with. Accounts with their own `holdings = [...]` were left out of the id lookup, so a snapshot stating such an account's id fell through to the file-name rule and was compared with the account it was named for (audit 2026-10-24 A13).
- **Fix:** upgrade: the id is looked up against every account first; a snapshot of an account whose `holdings = [...]` names its files is not compared (this Info says so) — list the file there, or drop the setting so `holdings/` is used. Nothing in the books changes: `tjs sanity` compares positions only.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/holdings_dir.py` — `discover`, `broker_ids`

### `tjs sanity`: "Error: holdings.toml: [[holding]] #3 (ZZQ270115C00010000.US): contract_multiplier -50 is not a positive number — fix the file"; on an older release an option holding showed a NEGATIVE cost
- **Check:** the holding's `contract_multiplier` (or `multiplier`) in the holdings TOML is 0 or negative.
- **Cause:** a contract size is a positive count of units; the reader took any number and multiplied the per-share `average_entry_price` by it, so a negative size made the position's cost negative (audit 2026-10-24 B36). `taxjson-export` refuses the same value when it reads its own file back.
- **Fix:** correct the file (100 for a standard equity option, the broker's figure for a mini or an adjusted series), then `tjs sanity`. An unreadable row is never skipped: `tjs sanity` stops with this Error (exit 2) and compares nothing, as for a size that is not a number or a `contract_multiplier_unknown` that is not `true` / `false` or sits beside a size (a malformed cost field elsewhere in the file no longer hides it; audit 2026-10-25 robustness#2).
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/positions_reports.py` — `_read_toml`, `is not a positive`, `PositionsSnapshotError`; `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `PositionsSnapshotError`

### `tjs sanity`: "Error: holdings.toml: [meta] holdings_count = 12 but the file has 7 [[holding]] table(s) — the snapshot is incomplete or edited; download it again"; on an older release the cut-short file compared "OK"
- **Check:** `[meta] holdings_count` in the holdings TOML is not the number of `[[holding]]` tables in it (count them: `grep -c '^\[\[holding\]\]' holdings.toml`).
- **Cause:** `tjs fetch --positions` and `taxjson-export` write the number of holdings they wrote; a copy that stopped part-way, or a hand edit that removed or pasted tables, leaves the count and the tables apart. sanity compared the tables present as if they were the whole snapshot: positions missing from the file showed as MISSING_IN_HOLDINGS, and one the broker holds and the books lack (missing history) went unseen behind "OK" (audit 2026-10-24 B37; the first fix refused only the file's cost and still compared its quantities, audit 2026-10-25 brokers-inputs#1).
- **Fix:** download the snapshot again (`tjs fetch --positions`, or the broker's export); for a hand-made file, fix or remove `holdings_count`. The file is refused until the two agree: `tjs sanity` stops with this Error (exit 2) and compares nothing, the checklist's sanity step needs attention, and `tjs run` ends with "Warning: holdings check could not run".
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/positions_reports.py` — `_read_toml`, `holdings_count`, `PositionsSnapshotError`; `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `PositionsSnapshotError`

### "Warning: 3 positions differ from the broker's holdings files"
- **Check:** `tjs sanity` lists each symbol with TAXJSON and HOLDINGS quantities and an ISSUE: `QTY_MISMATCH`, `MISSING_IN_TAXJSON` (the broker holds it, the books do not), `MISSING_IN_HOLDINGS`.
- **Cause:** fewer shares in taxjson than at the broker usually means missing history (purchases before the exports start, or shares transferred in). A trade after the last export, a symbol spelled differently (`QZQ.TO` vs `QZQ.US`) or a holdings file of another date are the other usual causes.
- **Fix:** `tjs find-missing-history` and `tjs transfers` for missing history; a `ticker.map` line for a spelling difference; re-export when the holdings file is newer than the activity.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_sanity_console`, `MISSING_IN_TAXJSON`; `src/taxjson/lib/positions_check.py`

### `tjs run` ends with "Warning: holdings check could not run" after an "Error: …" naming a holdings file; on an older release "holdings check returned no result ('')" (`--details`), or "positions differ from the broker holdings files … missing history" in captured output
- **Check:** `tjs sanity` stops with the same Error (exit 2): a holdings file it refuses (a `holdings_count` that is not its number of tables, a contract size that is not a positive number, a date that is no calendar day, snapshots of one account at different dates that do not name their broker account).
- **Cause:** the run's positions check read sanity's refusal of an input (exit 2, its Error already printed) as a check that returned nothing, or, in captured output (`TAXJSON_WIDTH=0`), as a position difference (audit 2026-10-25 first-project#3).
- **Fix:** fix the file the Error names, then `tjs sanity`. The run now says "The Error above names the file to fix; then `taxjson sanity`." Nothing in the books changes.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_sanity_console`, `_SANITY_REFUSED`

### `tjs sanity`: "Error: margin_live_holdings.toml has no [[holding]] array (a holdings file has [[holding]] tables)" for a snapshot of an account that holds nothing
- **Check:** the file has a `[meta]` table and no `[[holding]]` table (`holdings_count = 0`): `taxjson fetch --positions` wrote it after every position of the account was closed.
- **Cause:** sanity read a holdings file without `[[holding]]` tables as a broken file, so the positions check stopped for every account once one of them held nothing.
- **Fix:** upgrade: a snapshot with only its `[meta]` (no `holdings_count` above 0, no other table) is an account that holds nothing, and any position the books still hold there is listed as a difference. A file with a `[[holdings]]` typo, another table or a `holdings_count` above 0 and no rows is still refused. On an older release, move the file out of `holdings/`.
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/lib/holdings_dir.py` — `empty_snapshot`; `src/taxjson/lib/positions_reports.py` — `_read_toml`; `src/taxjson/bin/taxjson_run.py` — `has no [[holding]]`

### `tjs sanity`: "Info: holdings/download.toml: no account claims it — add its broker account id to the account" although the account declares the snapshot's broker id
- **Check:** the file's `[meta] broker_account` is in the account's `account` or `broker_accounts`, and its `[meta] account` is another name (the taxjson account's name when the snapshot was fetched: the account was renamed since, or the file was).
- **Cause:** the snapshot was matched by its `[meta] account` first, and `taxjson fetch --positions` writes the taxjson account's name there, not the broker's id; the broker id in `[meta] broker_account` was only read when `account` was missing.
- **Fix:** upgrade: `[meta] broker_account` is matched first, then `[meta] account`, then the file name. On an older release, name the file `<account>_holdings.toml` or list it in the account's `holdings = [...]`.
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/lib/holdings_dir.py` — `discover`, `broker_account`

### `taxjson-export h.toml --report` shows a cost of 0 for a holding of two listings (a JOURNAL fold), and `--holdings-toml` rewrites `total_cost = 0.0`
- **Check:** the holdings TOML has `mixed_currency = true` with `total_cost_usd` / `total_cost_cad` lines for the holding.
- **Cause:** re-reading a file it wrote, the tool ignored the per-currency figures of a cross-currency bucket and read its cost as 0, silently (2026-10 audit #64).
- **Fix:** upgrade: the per-currency figures are read back into the bucket; a mixed holding with none is refused, naming it. On an older release, read the costs from the file itself.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/bin/taxjson_export.py` — `_holdings_toml_to_inventory`

### A `[[distributions]]` entry dated on an opening snapshot's own day changes the snapshot's cost: "NOTE: [[distributions]]: … is dated on the opening snapshot's own day — applied on top of the snapshot's cost"
- **Check:** the entry's date equals the `OPENING` line's date for that symbol in the account.
- **Cause:** the snapshot's quantity holds the shares, but whether the statement's book cost already carries that day's distribution cannot be told (a year-end statement's often does not); it was applied without a word (2026-10 audit #26).
- **Fix:** the entry is applied and the NOTE says so: if the statement's book cost already includes it, remove the entry (CA-OPEN-03).
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/bin/taxjson_apply_distributions.py` — `apply_distributions`, `_snapshot_on`

### `tjs sanity`: an impossible `as_of` date (2025-03-32) is accepted and the check reports clean
- **Check:** look at `[meta] as_of`, `generated_at` and each `acquired` in the holdings TOML: each must be a real calendar day.
- **Cause:** the holdings TOML reader checked only the YYYY-MM-DD shape, not the calendar.
- **Fix:** upgrade; the file is then refused with an error naming the field. Correct the date.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/positions_reports.py` — `_iso_day`, `toml_as_of`

### `tjs sanity`: an impossible `acquired` date (2024-13-01) in a holdings TOML gives "result: OK"; only `--details` says "acquired '2024-13-01' is not a calendar date … its cost is not compared"
- **Check:** each `acquired` in the holdings TOML must be a real calendar day; `tjs opening` refuses the same file.
- **Cause:** sanity took the reader's refusal of the date for a malformed cost field: it compared the quantities, skipped the cost with a note and reported clean. An impossible date is now refused, as an impossible `as_of` is.
- **Fix:** upgrade; sanity then stops with an error naming the holding and its `acquired`. Correct the date.
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/lib/positions_reports.py` — `PositionsDateError`; `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `except (PositionsDateError, PositionsSnapshotError)`

### `tjs sanity`: a holdings TOML gives MISSING_IN_TAXJSON and no "as of" line although its `as_of` is set
- **Check:** a cost field in the file (`total_cost`, `cost_per_share` ...) is not a number; newer releases print a note naming it.
- **Cause:** when the positions reader refused the file's cost, sanity dropped the file's date and compared the latest books.
- **Fix:** upgrade; the file is then compared at its own date and its cost is skipped with a note. Correct the cost field to compare costs.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `_unread_cost`, `_toml_day`

### `tjs sanity`: `QTY_MISMATCH` with a HOLDINGS count that is the sum of two snapshots (taxjson 20, holdings 30) when `holdings/` keeps two dated snapshots of one account; or `tjs run` warns "1 position differs from the broker's holdings files" after a second snapshot was saved
- **Check:** `tjs sanity` lists two files for the account (two `file:` lines) with different `as_of` dates and the same `[meta] broker_account`; the HOLDINGS column is their sum.
- **Cause:** a group's files were always summed (one taxjson account across several broker accounts), snapshots of one broker account at two dates included: a correct January 10 and February 20 compared as 30 against the current 20 (2026-10-24 audit B17).
- **Fix:** upgrade: several snapshots of the same broker account(s) are compared one date at a time, each against the books on its own day, with an "Info: … each is compared with the books on its own day, never summed" note. Such snapshots beside other files of the group are refused ("… beside other files of the group", an entry below), and so are snapshots at different dates that do not name their broker account (next entry). On an older release, keep one snapshot per account in `holdings/`.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `_file_broker`, `_file_day`, `never summed`

### `tjs sanity`: `QTY_MISMATCH` of a spin-off's new shares by a fraction (TAXJSON 10.5, HOLDINGS 10) on a snapshot dated between an s.86.1 spin-off and its cash in lieu (a December spin-off whose fraction is paid in January)
- **Check:** `tjs list <snapshot date>` holds the fraction; the cash-in-lieu row is dated after the snapshot; the election is `rollover_s_86_1`.
- **Cause:** the rollover books the fraction with the whole shares on the spin-off date and sells it for the cash on the day it is paid (`tax-logic` CA-CORP-05), while the broker's snapshot shows the whole shares only; sanity compared the raw quantities (backlog TJ-0149).
- **Fix:** upgrade: a difference that is exactly such fractions is no discrepancy; a `fraction` line names it ("ZZS.TO: 0.5 more in the books, a spin-off's fraction (s.86.1, received …) sold for its cash in lieu on …, after this date — not a difference"). Any other difference is still reported.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/positions_check.py` — `fractions_awaiting_cash`; `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `fractions_awaiting_cash_in_lieu`

### `tjs sanity`: "Error: margin: margin_2024-06-30.toml (2024-06-30), margin_2024-12-31.toml (2024-12-31) are dated differently and none names its broker account"; on an older release `MISSING_IN_TAXJSON` (exit 1) for two correct snapshots, after "Info: margin: the files are dated differently … summed and compared with the latest books"
- **Check:** `holdings/` (or the account's `holdings = [...]`, or `tjs sanity margin=A.toml+B.toml`) has two or more files of one account with different `[meta] as_of` dates, and at least one has no `[meta] broker_account` (it is named only by `[meta] account = "margin"` or by its file name).
- **Cause:** one broker account at two dates (compared one date at a time) cannot be told from two broker accounts of one taxjson account (`margin_ib_holdings.toml` and `margin_wb_holdings.toml`, summed) without each file's broker account, and their snapshots can be days apart around the year end. sanity summed them: a correct mid-year and year-end pair compared as one snapshot gave a false MISSING_IN_TAXJSON and sent you to `tjs find-missing-history` (audit 2026-10-25 first-project#2). Either guess gives a false result for the other case, so the files are refused.
- **Fix:** add `broker_account = "…"` (the broker's account number; shown masked) to each file's `[meta]`: one broker account's snapshots are then compared one date at a time, each against the books on its own day, and different broker accounts' are summed. Or keep one snapshot per broker account (the year-end one), or check one file at a time: `tjs sanity margin=FILE`. Files of one date are summed as before.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `_file_broker`, `names its broker account`

### `tjs sanity`: "Error: the books of 'margin' cannot be read (work/margin_base.json: not JSON (…)) — re-run `taxjson run` to rebuild them"; on an older release a dated snapshot compared clean against books that could not be read
- **Check:** `work/margin_base.json` is there but is not valid JSON (cut short by a full disk or an interrupted run, or edited); `tjs sanity --json` (older release) shows the group with `"as_of": null` although the snapshot has an `as_of` date.
- **Cause:** the account's merged books (`work/<account>_base.json`) that were there but unreadable were read as an empty history, so a snapshot dated before the books' last day was compared with the latest books instead and certified clean and complete (2026-10-24 audit B18).
- **Fix:** `tjs run` rewrites the file. Unreadable books now stop `sanity` with this error (no clean verdict); books that are missing altogether are still no history, said in a note when a dated file is compared ("the books' rows (work/<account>_base.json) are missing: compared with the latest books").
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/positions_check.py` — `BaseBookError`, `load_base_rows`, `book_rows`; `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `_rows_of`

### `tjs sanity`: "Warning: work/margin_gains.json: QZQ.US: inventory qty nan is not a finite number" then "Error: work/margin_gains.json: QZQ.US: … — the books of 'margin' cannot be read: re-run `taxjson run` to rebuild them" (v0.35.0: "Error: taxjson.toml: the books of 'margin' cannot be read (…)"); on an older release the position vanished and the check was clean
- **Check:** `grep -n 'NaN\|Infinity' work/margin_gains.json` finds the quantity in the gains file's `inventory`.
- **Cause:** a quantity that is not a finite number fails every comparison, so the position dropped out of the books' side and an empty snapshot compared clean and complete (2026-10-24 audit B19). The gains file is damaged or hand-edited: `taxjson run` never writes one.
- **Fix:** `tjs run` rebuilds the file. The books are now refused as unreadable, like a gains file that is not JSON. v0.35.0 began the error with `taxjson.toml:` (where the account's `holdings` is set) although the damaged file is the gains file; the error now starts with that file.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `is not a finite number`, `the books of {name!r}`

### `tjs sanity`: "Error: the books of 'margin' cannot be read (work/margin_base.json: not a books document (no transactions list)) — re-run `taxjson run` to rebuild them"; on an older release a dated snapshot compared "OK" when that file held `{}` or `[]`
- **Check:** `head -c 200 work/margin_base.json`: the books `taxjson run` writes are a JSON object with a `transactions` list; `{}`, `[]` or an object without that list is not one (a hand edit, or a tool that wrote an empty document).
- **Cause:** the fix for books that are not JSON (entry above) still read `{}` and any bare list as an empty history: a snapshot dated before the books' last day was compared with the latest books instead, and a wrong snapshot was certified "OK: tickers and quantities agree" (backlog TJ-0010).
- **Fix:** `tjs run` rewrites the file. Such books now stop `sanity` with this error; books that are missing altogether are still no history.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/positions_check.py` — `load_base_rows`, `not a books document`, `BaseBookError`

### `tjs sanity`: "INCOMPLETE: 1 account(s) with `holdings` in taxjson.toml not checked (the notes on stderr): fix the paths, then re-run" when the only note is "Info: holdings/….toml: broker account 99*** is other's, whose own `holdings = [...]` setting names its files"
- **Check:** `tjs sanity --details`: no note says "holdings file(s) missing" or "`holdings` must be a path"; the account with `holdings = [...]` shows its group with a `result`.
- **Cause:** every note about the holdings set-up counted as an account not checked, the one about a `holdings/` snapshot of an account compared with its own `holdings = [...]` files included: nothing was left unchecked and no path was wrong (backlog TJ-0020).
- **Fix:** upgrade: that note is an Info only (in `tjs run` too, never "holdings check incomplete"), and `--json` `complete` stays true for it. `INCOMPLETE` counts the accounts whose `holdings` file is missing or unreadable and, said apart, a `holdings/` snapshot no account (or two) claims ("INCOMPLETE: 1 snapshot(s) in holdings/ not compared"). To quiet the note, list the file in that account's `holdings = [...]`, or drop the setting so `holdings/` is used.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `open_notes`, `_sanity_items_from_config`; `src/taxjson/lib/holdings_dir.py` — `discover`

### `tjs run`: "Warning: holdings check incomplete: account margin: holdings file(s) missing — not checked: …" then "Info: positions match the broker's holdings files (every checked account)"
- **Check:** `tjs sanity --json`: `accounts_not_checked` names the account; the file its `holdings = [...]` names does not exist.
- **Cause:** the run's closing line named as unchecked only the accounts with open positions and no holdings file; an account whose configured file is missing and that holds nothing in the books was left out, so "(every checked account)" read as all of them (backlog TJ-0064).
- **Fix:** upgrade: the line says "(checked accounts only; 1 unchecked: margin)". Fix the path in `holdings = [...]`, or drop the setting.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_sanity_console`, `accounts_not_checked`

### `tjs sanity`: "Error: margin: margin_x_dec.toml, margin_x_jun.toml are snapshots of one broker account at different dates (2024-06-30, 2024-12-31), beside other files of the group"
- **Check:** `ls holdings/` (or the account's `holdings = [...]`): one broker account has snapshots at two dates, while another broker account of the same taxjson account has one at only one of them.
- **Cause:** one broker account's snapshots at several dates are compared one date at a time only when the group's other files are at each of those dates; here a date lacks the other broker account's snapshot, so neither the sum nor one date's files is a whole snapshot of the account. Older releases advised "pair each date's files in a group of their own (ACCOUNT=FILE)", which gives the same error: a repeated `margin=` merges into one group (backlog TJ-0065).
- **Fix:** keep one snapshot per broker account (the year-end ones) in `holdings/`; or check a date whose files cover every broker account in a run of its own, with the command the error names (`tjs sanity margin=holdings/margin_x_dec.toml+holdings/margin_y_dec.toml`).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `beside other files of the group`, `a run of its own`

### `tjs sanity`: `QTY_MISMATCH` for a mid-year snapshot of one broker account beside another broker account's year-end one: "Info: margin: ZZA.TO changed after 2024-06-30, the date of margin_x_jun.toml: compared with the latest books"
- **Check:** the account's snapshots name different `[meta] broker_account`s at different `as_of` dates, and the difference is in a symbol traded after the earlier date.
- **Cause:** by design, different broker accounts' snapshots of one taxjson account are summed and compared with the latest books: the books do not say which broker account a trade was in, so they cannot be rebuilt per broker account on each file's date. A trade after the earlier snapshot shows as a difference; the Info line (new) names the symbols that changed after the earliest date (backlog TJ-0066).
- **Fix:** keep each broker account's snapshot of one date (the year end).
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `the books cannot tell which`

### `tjs checklist`: "! 22 sanity: COST — books vs the reports' cost: …" (or "BOOKS here is the native USD books …", "Not compared: … (the report states no cost)") while `tjs sanity` lists quantity differences
- **Check:** `tjs sanity` exits 1 with `QTY_MISMATCH` / `MISSING_IN_…` rows; the COST section after them is informational.
- **Cause:** the checklist's sanity step showed the last line of `tjs sanity --details`, which is the COST section (or its footnote) whenever a cost was compared, not the quantity differences that set the exit code (backlog TJ-0082, TJ-0094).
- **Fix:** upgrade: the step names the differences ("2 differ: ZZB.TO (margin), ZZB.TO (margin2)"). `tjs sanity` lists them: fewer shares in the books usually means missing history.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/checklist.py` — `d_sanity`, `_sanity_differences`

### `tjs sanity --json` prints "UNCHECKED: 5 account(s) with books and no holdings file: … — nothing to compare with" (text, not JSON) when no account has a holdings file, and the checklist's sanity step is `[m]`
- **Check:** `holdings/` holds no `.toml` and no account in taxjson.toml has `holdings = [...]`.
- **Cause:** with nothing to compare, sanity printed its text whatever the flags, counted only the taxable accounts (the run counts every account with positions), said `taxjson init` wrote holdings/README.txt (an `init --single` project has no holdings/), and the checklist made the step one to confirm, so `--done sanity` closed it with nothing compared (backlog TJ-0135, TJ-0136).
- **Fix:** upgrade: `--json` is a document like the compare's (`complete` false, `uncovered_accounts` naming every account with positions); `UNCHECKED` counts every account with positions (`--details` lists each); the checklist step needs attention, as when one account has a file. Save the broker's positions in `holdings/` (the "5 accounts with positions not checked" entry above says how), or accept it unchecked with `tjs checklist --done sanity --force`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `nothing to compare with`; `src/taxjson/lib/checklist.py` — `d_sanity`

### `tjs sanity`: "Info: margin_holdings.toml: [[holding]] #1 (ZZQ.TO)" with nothing after it
- **Check:** `tjs sanity --details` shows the whole note: a cost field of that holding (`total_cost`, `cost_per_share`, …) is not a number.
- **Cause:** the default view shows a note up to its first clause break; this note's was right after the file and row, so the reason fell off (backlog TJ-0160).
- **Fix:** upgrade: the line reads "… #1 (ZZQ.TO) cost is not compared: total_cost 'n/a' is not a number". The quantities are compared either way; correct the field to compare the cost.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_sanity`, `_unread_cost`, `cost is not compared`

### `tjs sanity`: "INCOME ON SHARES THE BOOKS DO NOT HOLD" misses (or shows too high a BOOKS count for) a dividend on a ticker that a reused old ticker was renamed into
- **Check:** the books have two renames of one old ticker (OLD -> NEW, then later OLD -> OTHER after OLD was reused), and the dividend is on OTHER, earned before the second rename.
- **Cause:** the rename chain was undated, so OTHER pulled in the shares that had already left OLD for NEW.
- **Fix:** upgrade. Renames are followed by date: only the shares the reused ticker held at its own rename count.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/positions_check.py` — `held_as`, `income_share_mismatches`

### `taxjson-export --holdings-toml` with the same `--transfer-evidence` sidecar named twice moves a journal's shares twice (two "note: applied evidenced depot flip: 5 QZD.US -> QZD.TO" lines for one journal)
- **Check:** the command line names one sidecar twice, or by two spellings of its path (`tjs run` names each sidecar once, so its `reports/<account>_holdings.toml` never had this).
- **Cause:** every named sidecar was read, so one file's legs counted twice.
- **Fix:** upgrade: each sidecar is read once, and identical rows of two sidecars count the way one broker's overlapping exports do (once per content id).
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_export.py` — `_evidence_rows`, `_apply_transfer_evidence`

### `reports/<account>_holdings.toml` moves shares to the other listing on the strength of transfer legs years apart: "note: applied evidenced depot flip: 5 QZD.US -> QZD.TO (transfer sidecar)." with no journal between the listings near either leg
- **Check:** `tjs transfers` (or the sidecar `work/<account>_<broker>_transfers.json`) shows the out-leg of one listing and the in-leg of the other dated far apart (a 2020 departure, a 2026 arrival): two custody moves, not one journal.
- **Cause:** the transfer legs were netted per listing over all time and the residuals paired by quantity alone, so legs years apart read as one journal and moved today's shares to the other listing.
- **Fix:** upgrade: legs pair as one journal only within 5 business days of each other (the window one journal's legs post in); a leg with no partner moves nothing, and legs that would pair but for the window are named in a `note:` line ("… are more than 5 business days apart: not read as one journal between the listings"). A journal the broker posted in rows further apart: declare it with a `.tt` `JOURNAL` line.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_export.py` — `_evidenced_journals`; `src/taxjson/lib/cross_listings.py` — `PAIR_DAYS`

### `reports/<account>_holdings.toml` keeps a short position under the old listing after the broker journaled it to the other listing
- **Check:** the holding is negative (`quantity = -10.0`) under the listing it left, and `tjs transfers` shows the journal's legs as +10 on that listing and -10 on the one it reached.
- **Cause:** the evidence walk moved long positions only: with nothing long under the listing the units left, the journal moved nothing.
- **Fix:** upgrade: a journaled short moves to the listing it reached ("note: applied evidenced depot flip: -10 QZD.US -> QZD.TO (a short position; transfer sidecar)."), its base-currency cost with it.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_export.py` — `_apply_transfer_evidence`, `_move_units`, `_replay_moves_on_base`

### `reports/<account>_holdings.toml` gives an option `contract_multiplier = 100` (and `taxjson-export h.toml --report` a cost/share divided by 100) although its inputs disagree on the contract size
- **Check:** the run's `.diag` (or the export's stderr) has "warning: QZK250620C00010000.US: its inputs declare different contract sizes (25, 100)".
- **Cause:** two inputs declaring different sizes left the declared size unused, and the equity option's 100 took its place: an invented size where the size is uncertain.
- **Fix:** upgrade: the size is left unknown. The snapshot has `contract_multiplier_unknown = true` and a comment naming the sizes seen instead of `contract_multiplier` (read back as unknown too), and the report's cost/share stays per contract. Then make the inputs agree (the row that declares the wrong size, or a holdings file's `multiplier`).
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_export.py` — `contract_multiplier_of`, `process_data_report`, `_holdings_toml_to_inventory`

### `reports/<account>_holdings.toml`: a position that went from long to short (or short to long) in one trade lists the old position's trades in `trades`
- **Check:** the holding's `trades` start before its `position_start_date`, with a buy sold out by a later, larger sale.
- **Cause:** the current round's trades were reset only when the balance came back to exactly zero; a trade crossing from long to short started no new round.
- **Fix:** upgrade: a crossing trade starts the new position and is kept as the part that opened it (10 held, 20 sold: a `SELL` of 10 at its price), as the engine's `position_start_date` has it.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_export.py` — `_load_trade_events`

### `reports/<account>_holdings.toml`: `base_total_cost` in the base currency for a holding whose base-currency figures are in two currencies ("warning: QZD.US: its base-currency figures are in CAD, USD: base_total_cost is omitted for it")
- **Check:** the run's `.diag` has that warning and the holding a `# base_total_cost omitted` comment (on an older release: a `base_total_cost` that is a CAD + USD sum, labelled CAD).
- **Cause:** the base-currency aggregate was labelled with the base currency although a cost in it was never converted (two base inputs for one symbol in different currencies).
- **Fix:** upgrade: the figure is omitted; the comment and the warning name the currencies. Then look at why a cost of that holding was not converted: the run's FX warnings name the rows.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_export.py` — `render_holdings_toml`

### `reports/<account>_holdings.toml`: after a ticker change, `trades` lists the old ticker's earlier trades after the new ticker's later ones
- **Check:** the holding of the new ticker has `trades` whose dates go backwards at one point, and the books have a rename (`tjs events`) while the new ticker already had trades of its own.
- **Cause:** at the rename the old ticker's events were appended behind the new ticker's.
- **Fix:** upgrade: the two are merged in the order the trades are replayed (settlement date, then the file's order).
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_export.py` — `_load_trade_events`

## Corporate actions and income

### A tiny share position's sale is in no total: "warning: conservation: SAMP.US pool is EMPTY but carries 100.00 of stranded basis" in the account's `.sum`
- **Check:** the position is under a millionth of a share (`tjs events all ACCOUNT`), and `tjs sum` shows no disposition for its sale.
- **Cause:** the Canadian engine reads a position under 1e-6 units as flat (rounding noise), so the sale opened a short and the purchase's cost was stranded (2026-10 audit #101). Brokers do not export such rows; a hand-written `.tt` line can.
- **Fix:** upgrade: a position under the tolerance that still carries a cent or more of cost is kept after its buy and closed by its sale at its cost. On an older release, enter the quantity with more units (the whole lot) or record the disposition by hand.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/core.py` — `_dust_close`, `_opened_dust`, `pool_qty_eps`

### After a merger held in two taxable accounts (US project), `tjs sanity` shows an account short the new shares it received, Form 8949 understates the gain of the account that sold them, and `tjs run --details` has "ATTENTION ... share-conservation" (or, Canada, `tjs t1135` shows a smaller cost amount than `tjs run`'s ACB for the new shares)
- **Check:** `tjs run --details` says `NOTE: QZOLD.US -> QZNEW.US on 2024-06-03: per-account merger ratios [1, 1.05] applied ...` (Canada) or names the merger in each account; the accounts received different whole-share counts per old share (12 old became 12 new in one account, 40 became 42 in another). US: the account that received more shares than the first account's ratio gives sells them and goes short. Canada: `tjs t1135 --json` `per_symbol` cost for the new shares is below the engine's ACB in the inventory.
- **Cause:** the merger's rows carry each account's actual delivery ratio. US: the first account's row scaled and renamed every account's lots at its ratio and the second account's row found nothing to rename, so that account held fewer shares than delivered and its sale opened a phantom short at no gain. Canada's gains engine folds the rows into one event at the holdings-weighted ratio, but the T1135 cost walk replayed them raw, with the same loss of shares, and a sale then released too much cost.
- **Fix:** upgrade. US: each row reaches its own account's lots (an account with no row of its own takes the first ratio; `--per-account-basis`, which `tjs run` passes), and without per-account basis the rows fold as Canada's do. The T1135 walk folds them as the gains engine does, on the project's date basis. Then `tjs run`.
- **Fixed in:** `v0.30.4`
- **Code:** `src/taxjson/lib/core.py` — `_per_account_rename_ratios`, `_split_covers`, `_fold_per_account_rename_ratios`; `src/taxjson/bin/taxjson_t1135.py` — `_fold_merger_ratios`

### Canada: a merger with per-account ratios on shares that had been renamed earlier leaves `tjs sanity` short the new shares and `tjs sum` with a smaller gain on their sale; `tjs run --details` has no "per-account merger ratios" NOTE for it
- **Check:** `tjs events all | grep SYMBOL` shows a rename (`SPLIT ... OLD NEW 1`) before the merger's per-account `SPLIT` rows, and no purchase under the intermediate ticker. `tjs sanity` on the new shares is short exactly the second account's extra shares.
- **Cause:** the fold that weights a merger's per-account ratios by each account's holdings read only the rows of the merger's own symbol: shares bought under an earlier ticker and renamed into it weighed nothing, so no weights were found, the rows were not folded, and the first row renamed the whole pool at its ratio (2026-10 audit R01).
- **Fix:** upgrade and `tjs run`: the weights replay every rename (and own-account move) up to the merger, so the shares renamed into the merger's source count.
- **Fixed in:** `v0.30.4`
- **Code:** `src/taxjson/lib/core.py` — `_fold_per_account_rename_ratios`, `_weights`, `seen_rename`

### `tjs stats` / `tjs win-rates --by symbol`: after an s.86.1 spin-off of shares that carried a denied loss, the parent shows a larger win and the spun-off shares a larger loss than their sales (the total is right)
- **Check:** `tjs wash-sales` lists a denied loss on the parent before the spin-off; `tjs spinoffs` shows the `rollover_s_86_1` election with its `allocated_acb_cad`. In `work/<account>_gains_wash.json` the parent's later sale has the whole denied amount as `deferred_loss_recovered`, the spun-off shares' sale none.
- **Cause:** the spin-off moves part of the parent's ACB (the denied loss in it included) to the new shares, but the tally of earlier denied losses (`deferred_wash`) stayed with the parent, so the view took the whole loss back out of the parent's sale (backlog TJ-0062).
- **Fix:** upgrade, then `tjs run`: the share of the ACB the parent gives up carries the same share of its tally to the new shares, so each sale's P/L is counted on its own part. The filing figures do not change; `tjs list` shows the deferred loss split the same way.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/core.py` — `_cev_moves`, `_cev_stash`, `deferred_wash`

### `tjs stats` / `tjs win-rates`: a written option bought back shows a larger profit than its premium, or a written option that was split or renamed shows no trade at all
- **Check:** `tjs events all ACCOUNT | grep SERIES`: the buy-back row carries a negative amount (a `.tt` line entered with the money signed), or a `SPLIT` row renames or redenominates the series before its close. `tjs option-yield` shows the same contract with the right P/L.
- **Cause:** the statistics' matcher subtracted the signed amount as money paid, so a negative buy-back amount added to the profit; and it read only `BUYSELL` / `ASSIGN` rows, so a split or rename never moved the queued contracts and the close under the new series matched nothing (2026-10 audit R03, R04).
- **Fix:** upgrade: money is read as a magnitude, the quantity's sign saying which way it went, and a split or rename moves and scales the queued contracts (one event when two rows report it).
- **Fixed in:** `v0.30.4`
- **Code:** `src/taxjson/lib/trade_stats.py` — `written_option_trades`, `dedupe_split_events`

### `tjs stats` / `tjs win-rates` / `tjs option-yield`: a long option held before the data (a `.tt` `OPENING … cost=unknown` line) and sold shows as a write bought back, with a profit
- **Check:** `tjs stats --json` counts a written-options trade for the contract; the gains stage leaves its sale out as unknown-cost (`tainted_skipped`).
- **Cause:** the opening is synthesized in memory by the gains stage; the books the option views read never carry it, so the sale read as a write and the later buy as its buy-back (2026-10 audit #59).
- **Fix:** upgrade: the views add the same opening, so the sale closes it (unknown cost, counted, not a trade). On an older release, read the contract's result from `tjs sum`.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_option_openings`; `src/taxjson/lib/trade_stats.py` — `written_option_trades`

### `tjs stats all` (or `stats 2y`, `winners all`, `ccd-sum 2023`, `leaps-sum all`, `win-rates --all-history`) warns "the computed gains cover tax year N only — rows outside it are NOT in this report" and shows the tax year alone
- **Check:** `ls work/*_gains_all.json` lists nothing after the command; `tjs --version` is older than the release below. With a current release the command prints `Info: the window reaches outside tax year N: read from the full-history books` and the file exists.
- **Cause:** `taxjson run` computes the gains files for the tax year only (earlier years are read for cost, not written as dispositions), and the seven views that read them had no other source for a wider window.
- **Fix:** upgrade: a window outside the tax year builds `work/<account>_gains_all.json` on demand from the same consolidated base-currency book (`<account>_base.json`) through the gains engine with no year, and reads that: the rows inside the tax year equal the filing file's; the superficial-loss / wash-sale denials in it are the engine's own, within each account (the cross-account pass runs for the tax year). An account with no base book (a hand-made `work/`) keeps the warning. On an older release, run `tjs stats` in each year's folder, or `tjs gains all` (native currency, per listing).
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_window_gains_files`, `_gains_all_file`

### `tjs div-estimate 1m` (a short forward window) shows a smaller "Rest of tax year" total than `tjs div-estimate 12m` on the same books
- **Check:** the `Rest of tax year` figure changes with the window asked for; `--details` lists the projected payments only up to the window's end.
- **Cause:** a regular payer's payments were generated up to the forward window only, and the rest-of-year total summed that list (2026-10 audit R08).
- **Fix:** upgrade: the projection runs to the later of the window and the tax year's end, the window total is the window's payments and the rest-of-year total the year's, whatever window was asked for.
- **Fixed in:** `v0.30.4`
- **Code:** `src/taxjson/lib/div_estimate.py` — `estimate`, `reach`

### US project: after a stock dividend, Form 8949 shows a short-term loss (or gain) on shares bought after the dividend's record date, and the long-term gain on the old shares is lower than their basis gives; `tjs run --details` has "stock dividend of N share(s) ... spread over"
- **Check:** `tjs run --details` on the symbol: the "stock dividend" note names the shares whose basis was spread. A lot bought after the record date but before the pay date was scaled with the old ones (the trace, `tjs run --trace`, shows the old lot at 10.5 shares and the new lot at 10.5).
- **Cause:** the §307 basis spread reached every lot held on the pay date, not only the lots entitled to the dividend (bought before the ex-dividend date; IRS Publication 550, "Stock bought at various times"). A sale then took part of a lot it should not have, moving basis between the lots and gain between the short- and long-term parts.
- **Fix:** upgrade. With the row's `ex_date` (or `record_date`) the spread reaches the lots bought before it and the later lots keep their basis. A broker's row carries no such date: every lot held on the pay date still shares the basis, and a warning names a lot bought within the 90 days before the pay date as the open question (`ATTENTION: ... carries no record or ex-dividend date`, shown by `tjs run`); check the issuer's record date, and if a lot came after it, give the row its `ex_date` in a JSON book or correct the lots' basis by hand.
- **Fixed in:** `v0.30.4`
- **Code:** `src/taxjson/lib/core.py` — `is_stock_dividend`, `_ent_date`, `_recent_qty`

### "Info: split QZS.TO x2 is booked on two dates (2025-06-03 and 2025-06-04) — One corporate event reported by two sources; applied ONCE, on 2025-06-03.", and a `[[distributions]]` amount (or an account's share of a blended pool) looks too small
- **Check:** `tjs list` shows the `[[distributions]]` ADJUST (id `DIST-…`) on the record date; compare its amount with per_share x the shares you held that day. On releases before the fix it came out short when a sale executed before the split settled after it: the sale was scaled by the ratio once per copy of the split.
- **Cause:** two sources (two brokers, or a `.tt` SPLIT line next to the broker's row) report one split on nearby dates. The gains engine and the holding walk counted it once, but the settle-lag step that re-denominates a trade straddling the split multiplied by every copy, so the record-date holding (and the split-gains apportionment of a blended pool) was wrong and gains were overstated.
- **Fix:** upgrade: every walk of split events now uses one corporate-event rule (same symbol and ratio within a week = one event, kept on its earliest date), and `tjs run` again. Deleting the extra `.tt` SPLIT line also works on older releases.
- **Fixed in:** `v0.30.0`
- **Code:** `src/taxjson/lib/corporate_timeline.py` — `split_copies`, `dedupe_split_events`; `src/taxjson/bin/taxjson_apply_distributions.py` — `_settle_lag_factors`, `balance_on`; `src/taxjson/bin/taxjson_split_gains.py` — `split_for_account`

### `tjs splits` shows `held 10 -> 30` for a 2-for-1 split on a day with a purchase of 5 (or `tjs spinoffs` "held now" is off by the day's purchases), while `tjs run` and `tjs list` hold 25
- **Check:** the SPLIT row carries a clock time later than the day's trades (IB stamps corporate actions in the evening; `tjs events all ACCOUNT` shows the rows), and the view's figure is the pre-split holding plus the day's purchases, all scaled.
- **Cause:** the views replayed the account's rows by clock time, so a split stamped at noon doubled the shares bought that morning; the engines and the [[distributions]] balance walk apply a split at the market open, before the day's trades (CA-DATE-14 / US-DATE-13), whatever its stamp (audit 2026-10-24 B05).
- **Fix:** upgrade: the views replay through the same balance walk (`balance_on`, trade dates). Nothing in the books changed: the view only.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/corp_views.py` — `_held`; `src/taxjson/bin/taxjson_apply_distributions.py` — `balance_on`

### "Error: 1 corp-action event(s) need an election — not asked: --no-input" (or "… not asked: no terminal to answer on"), and `tjs run` exits 3
- **Check:** `tjs elect --pending` lists each event with what each election books and the hints it needs (also in `work/pending_elections.json`).
- **Cause:** a spin-off or merger has more than one tax treatment and only you can choose. The run defers the account until it is chosen; with `--no-input`, or run from a script or an AI assistant (no terminal to answer on), it cannot ask, and the message says which.
- **Fix:** `tjs elect margin --set EVENT_ID=ELECTION` (add `--hint KEY=VALUE` where required), or run `tjs run` at a terminal to be asked; then run again. A spin-off or merger of a sheltered account is not asked by default (next entry); with `sheltered_elections = "ask"` it is — the election sets the cost its holdings carry, and no tax in the account depends on it: the prompt and `tjs elect --pending` say "sheltered account: this election sets the holdings' cost in the books — no tax in this account; its holdings still count for the superficial-loss rule" (a US project: the wash-sale rule).
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_corp_actions.py` — `corp-action event(s) need an election `, `sheltered_note`; `src/taxjson/bin/taxjson_run.py` — `cmd_elect`, `_print_pending`

### "Error: rollover_s_86_1 needs allocated_acb_cad" from `tjs elect ACCOUNT --set EVENT_ID=rollover_s_86_1`, and `tjs elect --pending` then says "No pending elections"
- **Check:** `tjs elect ACCOUNT` lists the event with an election saved: it is elected already (a late re-election), so it is no pending election — `--pending` lists only the events a `--no-input` run deferred.
- **Cause:** the election you chose books a figure only you can give (`allocated_acb_cad`: the cost moved to the spun-off shares). The refusal sent you to `tjs elect --pending` for what the hint means, which says nothing about an event already elected.
- **Fix:** the message says what each hint it needs is (docs/settings.md lists every election's hints). For `allocated_acb_cad`: the parent's ACB in CAD just before the spin-off (`tjs list`) × the spun-off shares' FMV / (the parent's FMV + the spun-off shares' FMV) just after it. Then `tjs elect ACCOUNT --set EVENT_ID=rollover_s_86_1 --hint allocated_acb_cad=<CAD amount>` and `tjs run`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_elect`, `event_hints_by_option`; `src/taxjson/lib/corp_actions.py` — `HINTS_BY_ELECTION`

### "Info: sheltered account lira: spin-off SPNCO.TO on 2025-03-03 (event …) booked at $0 cost for the distributed shares"
- **Check:** `tjs spinoffs` shows the event with election `sheltered_default` and the new shares' cost of 0; `tjs elect --pending` does not list it.
- **Cause:** a spin-off in a sheltered account (RRSP, LIRA, TFSA, RESP, RRIF; US: IRA, Roth, 401(k), HSA, 529) is booked without asking: nothing is taxed inside the account, and nothing taxable reads its cost (an in-kind move uses fair market value; the superficial-loss / wash-sale rule counts units). The new shares start at $0 and the parent keeps its whole cost; a merger's new shares take the old shares' cost ("booked with the old shares' cost carried to the new shares"). It used to be asked like a taxable account's event, stopping `run --no-input`, `run --strict` and the checklist until answered.
- **Fix:** nothing to do. For a real cost in the holdings view, `tjs elect lira --set EVENT_ID=ELECTION` (the saved election wins); `sheltered_elections = "ask"` in `[settings]` asks for every such event again.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_note_sheltered_defaults`, `_sheltered_elections`; `src/taxjson/lib/corp_actions.py` — `sheltered_default_rows`, `sheltered_default_text`; `src/taxjson/bin/taxjson_corp_actions.py` — `--sheltered-elections`

### `tjs elect lira` lists a "sheltered default ($0 cost for the distributed shares)" event of another account, one whose name starts with `lira_` (`lira_spouse`)
- **Check:** `tjs spinoffs lira_spouse` shows the event; `tjs spinoffs lira` does not.
- **Cause:** the election listing read the corporate-action files of every account whose name begins with the account's name and `_` (`work/lira_spouse_ib_corp.json` for `lira`), so a longer-named account's defaulted events were listed (and in `--json` counted) under the shorter one.
- **Fix:** upgrade: the listing reads the account's own files only. Nothing in the books changed: each account's run booked its own events.
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_defaulted_events`, `CORP_ACTION_BROKERS`

### "Warning: margin: spin-off SPNC.US on 2025-06-03 (event …) is booked at $0"
- **Check:** `tjs spinoffs` shows the election, the value used and the cost booked.
- **Cause:** the election has no `fmv_per_share` saved (a hand-edited or older manifest) and the broker reported no value: no dividend income is booked and the new shares cost $0, so a later sale overstates the gain. The same warning exists for a merger booked at $0 (there `fmv_per_share=0` still means "not known yet") and a spin-off with $0 allocated cost. A spin-off whose `fmv_per_share=0` you wrote yourself is a declared $0 cost: an Info ("… is booked at the $0 value you declared (fmv_per_share=0)"), nothing to do.
- **Fix:** set the value: `tjs elect margin --set EVENT_ID=ELECTION --hint fmv_per_share=<value>` (the line `tjs run --details` prints under the warning), then `tjs run`.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_warn_zero_value_spinoffs`

### `tjs spinoffs`: "the saved fmv_per_share=0 is not used: a 0 means no value was given, and the broker reported 970.00 USD for the new shares, which is the value booked"; on an older release the same spin-off showed "the broker's value" with no word about the `fmv_per_share=0` you had saved
- **Check:** `tjs elect ACCOUNT --pending --json` (or the account's `inputs/ACCOUNT/manifest.json`) saves `fmv_per_share: 0` for the event, and the broker's row carries a value for the new shares (IB does; Questrade and RBC do not). `tjs spinoffs` shows the income and cost booked at the broker's value.
- **Cause:** for a taxable spin-off the booking takes a positive `fmv_per_share`, else the broker's own value, else $0. A saved 0 means "no value given" while the broker reports one, since a 0 is what you enter when you do not know the value; it is a declared $0 cost only when the broker reports none. Such a 0 comes from a hand-edited or older manifest, or one saved before the broker's export carried the value: `tjs elect` asks for no value, and `--set` takes none, while the broker reports one. The view showed the broker's value without saying why the saved 0 was not it (audit 2026-10-24 B16).
- **Fix:** nothing to do: the broker's value is the one booked. Upgrade for the line that says so.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/corp_views.py` — `spinoffs`, `the saved fmv_per_share=0 is not used`; `src/taxjson/lib/corp_actions.py` — `_emit_distribution`, `declares_zero_value`

### `tjs spinoffs`: "value used: 20.00 CAD/share (the broker's value)" for a value the broker gave in US dollars; or "0.0952381 new per share (10 for 105)" while 10.5 new shares are booked
- **Check:** `tjs spinoffs --json` gives `broker_currency: "USD"` and `fmv_per_share` = the broker's value / the new shares; for the ratio, the export pays the fraction in cash (a CIL row) and `booked` counts the whole and fractional shares.
- **Cause:** the view printed the per-share value with the base currency whatever its currency, and the ratio from the delivered shares only (backlog TJ-0134, TJ-0166).
- **Fix:** upgrade: the value per share is in its own currency (`fmv_currency` in `--json`), the ratio counts the fraction ("0.1 new per share (10.5 for 105)"), and a "cash in lieu" line gives the fraction and the cash; `tjs elect --pending` says it too. Nothing booked changes.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/corp_views.py` — `spinoffs`, `render_spinoffs`, `cash_in_lieu_line`; `src/taxjson/bin/taxjson_run.py` — `_print_pending`

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

### "Warning: 2 trade(s) in QZOLD.TO after its rename to QZNEW.TO on 2025-04-01" for an account that never held QZOLD.TO, while another account's line says `late=fold`
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
- **Check:** `tjs run --details` adds "If both are the same distribution the cost is reduced TWICE." `tjs roc-sum` warns "has an ADJUST in the books AND a [[distributions]] entry".
- **Cause:** the broker export (or a `.tt` ADJUST line) already books the return of capital, and a `[[distributions]]` table in `taxjson.toml` books it again on the same date.
- **Fix:** keep one: delete the `[[distributions]]` entry (or the `.tt` ADJUST), then `tjs run`.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_apply_distributions.py` — `_warn_roc_overlaps`, `reduced TWICE`; `src/taxjson/bin/taxjson_run.py` — `_warn_dist_double_entry`

### `tjs run` prints "taxjson-apply-distributions: applied 2 adjustment(s) to margin_base.json.stage."
- **Check:** the line follows "==> Applying [[distributions]] from taxjson.toml" and the per-entry `Info:` lines.
- **Cause:** the stage's closing count was printed as it was: a program name, an internal file name and no label.
- **Fix:** nothing to do: the adjustments were applied. Since the fix the line is "Info: 2 [[distributions]] adjustment(s) applied to margin's books".
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_apply_distributions.py` — `main`, `[[distributions]] adjustment(s)`

### US project: a `[[distributions]]` basis adjustment lands on the oldest lot only (a later lot settled by the record date gets none), or `tjs run` says "warning: ATTENTION: [[distributions]] ZZQ.US 2026-01-05: the basis adjustment is dated 2026-01-01 so that it reaches the shares of the sale settling after the record date, it misses the lots of the trade(s) in between (bought 10 on 2026-01-03)"
- **Check:** `tjs run --details` prints the stage's `NOTE: ZZQ.US 2026-01-05: reinvested distribution (basis up) — 20 sh x 1 USD = +20.00 basis adjustment`; `tjs list` (or the `.diag`) shows the whole amount on the first lot and nothing on a lot bought after a purchase that straddled the record date (traded on or before it, settled after it). The ATTENTION line names the trades the stamp cannot reach.
- **Cause:** the stage sizes the adjustment on the settled position of the record date and, for the US engine's trade-date order, dated the row the day BEFORE the earliest trade straddling the record date — a buy as well as a sale. Before a straddling buy, only the lots bought earlier were open, so the whole amount went to them and a lot bought after the straddling buy but settled by the record date got nothing (audit 2026-10-24 B14). Canada's pool is unaffected: the amount lands in the pool whatever the stamp.
- **Fix:** upgrade: the row carries the record date and the US engine spreads it over the open lots settled by that date only, so a straddling buy needs no backdating; the row is dated before a straddling SALE only (its lot is still the holder's). When a purchase or a sale that settles by the record date falls between that sale and the record date, no single row can reach exactly the record date's lots: the ATTENTION line names those trades — check the lots' basis in `tjs list`, or enter per-lot `.tt` ADJUST rows by hand. Then `tjs run`.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_apply_distributions.py` — `_stamp_date`, `_stamp_problem`, `record_date`; `src/taxjson/lib/core.py` — `settled by the record date`

### US project: `tjs run` says "warning: ATTENTION: [[distributions]] ZZQ.US 2024-06-10: the basis adjustment is dated 2024-06-09 … it misses the lots of the trade(s) in between (bought 30 on 2024-06-10)" for a purchase that settles after the record date
- **Check:** the purchase named was traded on or before the record date and settles after it (the broker export's settlement date), and a sale of the same day settles after it too. `tjs form-export --form 8949` shows that purchase's lot with its own cost only, and the lots of record with the whole adjustment: the placement is exact.
- **Cause:** the check that names the trades a backdated adjustment cannot reach left out only the sales that straddle the record date. A purchase that straddles it is not the holder of record's and rightly takes none (US-DIST-01), so the warning named a miss that was the right placement and sent you to enter per-lot ADJUST rows for figures that were already right.
- **Fix:** upgrade; nothing to enter. The warning still names a purchase or a sale between the stamp and the record date that settles by the record date (a `.tt` row has its trade date as its settlement date): check those lots' basis in `tjs list`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_apply_distributions.py` — `_stamp_problem`, `_straddles`

### `tjs run`: the January return-of-capital warning is gone though this account has no .tt pair
- **Check:** search the other accounts' .tt files for `ADJUST <prev>-12-31 ... <SYMBOL>` and the matching January `ADJUST` line with the same amount.
- **Cause:** the pair was matched on symbol, date and amount only, so a pair in another account or currency silenced this account's warning.
- **Fix:** upgrade; then add the two ADJUST lines to this account's .tt if the T3 box 42 says the ROC belongs to December.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/income_dating.py` — `warnings`, `_adjusted`

### `tjs splits`: a split after a ticker change says "nothing held just before"
- **Check:** `tjs splits` lists an earlier rename (ratio 1, a new symbol) to the split symbol.
- **Cause:** the view counted only rows booked under the split symbol, not shares carried over by the rename. The engine's books were right.
- **Fix:** upgrade.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/corp_views.py` — `_held`

## Interactive Brokers

### IB: "Error: ib_2024.csv line 13 (Corporate Actions)" with "Section 'Corporate Actions' is missing required column(s) 'Code' — refusing to guess"
- **Check:** the section's `Header` row in the statement lacks the column named. It is usually a statement whose columns were edited in a spreadsheet, or a Flex query that leaves the column out.
- **Cause:** the IB parser reads each section by its header names and refuses a section that lacks a column it books from: a missing money column would read as 0, and without `Code` a cancelled row (code `Ca`) would read as a new event. Earlier releases gave the money-column reason for every column, `Code` included; the reason now fits the column.
- **Fix:** download the statement again in the standard English layout (the Activity Statement CSV, or a Flex query with every column of the section), and `tjs run`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/brokerages/base.py` — `require_columns`, `_MONEY_COLUMNS`; `src/taxjson/lib/brokerages/ib_extractor.py` — `Code carries the`

### IB: "Warning: U1234567.csv: no Cash Report, so its cash is not reconciled: add it to the export" (`--details`: "the statement has no Cash Report") or "Error: U1***.csv: parsed rows do not reconcile with IB's own Cash Report"
- **Check:** the file has no `Cash Report,Header,…` lines (a Flex query or a customised statement). For the error, the next line names the currency and the line, e.g. `USD Dividends: parsed 2.50 vs Cash Report 3.50 (diff -1.00)`.
- **Cause:** taxjson checks the money it parsed against IB's own Cash Report totals, per currency: dividends, payments in lieu, withholding, interest, other fees, commissions and trades. Without the section the check is off, and the run says so. A mismatch means a row was dropped, doubled or mis-signed (often an edited statement), and the parse stops.
- **Fix:** export the Activity Statement with the Cash Report section (Flex: add it to the query). On the error, download the statement again and put it in `inputs/<account>/` unedited. The repository's `examples/ib_demo.csv` lacked the section through v0.35.0 (the `tjs init --demo` copy has it); it is now the demo's file.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `no Cash Report — parsed money is NOT reconciled`, `parsed rows do not reconcile with IB's own`

### "Warning: Webull exports for margin end 2025-10-06 with 1 position(s) open: download the rest of 2026"
- **Check:** on a wide terminal, and with `tjs run --details`, the line names the positions: "Webull exports for margin end 2025-10-06 with open positions (QZT270115C00090000.US 14); download the rest of 2026". The details (`--details`) say how the end was read: an IB statement's Period, an RBC export's "as of" date, a Webull export's date range or trading-summary year, or — for a broker whose export names no end (Questrade, a generic mapping) — the last row. `tjs checklist` shows "! 5 export-coverage: Webull exports for margin end 2025-10-06 with 5 open position(s)... — tjs run", counting the positions instead of naming them; `tjs checklist --all` shows the whole line: "Webull exports for margin end 2025-10-06 with 5 open position(s) in Webull's rows — `taxjson list margin 2025-10-06` lists the account's positions on that day (every source); next recorded activity: 2026-06-18 (wb_2026_manual.tt)" (the run's Warning names the same command and date): `tjs list <account> <end date>` shows what was open at the end, the positions from the account's other sources (a `.tt` opening, another broker) among them, and the next activity a later `.tt` line or export of the account records on those positions (any date after the end) shows when they moved next.
- **Cause:** that broker's exports for the account stop before the tax year's end (before today in the year still running; an explicit end may trail today by 14 days) while it still holds positions there, so its later sales, option expiries and assignments, and income are not in the books — and nothing else says so. Only positions still open after the account's later rows of any source, dated inside the gap (after the end, up to Dec 31 of a finished year or today), are named: a `.tt` line that closes one (a hand-entered sale or expiry), another broker's sale or a transfer-out counts, and a broker whose positions are all closed is never listed — when `.tt` lines closed them, the run says "Info: … the positions open at the export end (…) were closed by .tt lines — no export needed" instead. An end read from the last row is flagged only after more than 30 days of the year with no row.
- **Fix:** download the broker's activity for the rest of the year into `inputs/<account>/` and `tjs run`. If the broker really had no activity in the account after that end (an account you stopped using), answer "No <broker> activity in <account> after <end>?" with `tjs checklist --done export-coverage` — whatever the end was read from; the run then says a note. The mark answers the gaps shown when you made it (account, broker and end; `checklist.json` keeps them under `answers`): a new gap, or a later end after you add an export that is still short, asks again. An option the warning lists as "expired after it with no expiry row" expired inside the gap: the missing export holds its expiry, assignment or buy-back.
- **Fixed in:** `v0.24.1`
- **Code:** `src/taxjson/lib/export_coverage.py` — `find_gaps`, `account_gaps`, `open_positions`, `held_at_broker`, `closed_later`, `info_message`, `message`, `question_text`, `Gap`, `detail`, `next_activity`, `list_command`; `src/taxjson/bin/taxjson_run.py` — `_say_export_coverage`, `_mark_answers`; `src/taxjson/lib/checklist.py` — `d_export_coverage`, `question_answers`, `apply_override`

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

### A merger's or spin-off's gain changes when two overlapping statements of one broker account are in the folder, or changes with their file names
- **Check:** the two statements report the event with the same quantities but a different FMV, target value or cash in lieu (a corrected statement beside the original). A current release prints `Warning: ATTENTION: corp-action <id>: two statements of broker account XX*** agree on its quantities but disagree on its values (FMV … vs …); kept the copy with FMV …` on the run's console, and `tjs run --strict` stops on it.
- **Cause:** copies of one holding were compared on quantities only, so equal-quantity copies kept whichever file was read first, silently (2026-10 audit #104).
- **Fix:** upgrade: the values are compared too; on a disagreement the larger copy is kept whatever the order, and the warning says what differs. Keep only the current statement in `inputs/<account>/`. On an older release, remove the outdated statement.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/corp_actions.py` — `combine_broker_copies`, `_value_differences`, `_larger_copy`

### IB: two overlapping statements of one account with a corrected corporate-action value dispose of the shares twice ("Warning: ATTENTION: corporate action '…' on 2026-06-11: two statements of broker account U5*** disagree on its value (… vs …); kept …")
- **Check:** two statements of the same IB account in `inputs/<account>/` list the merger's rows with the same quantities but another Value. On an older release the election shows twice the quantity (`FMV` the sum of both), the old shares go short, and the new position is doubled.
- **Cause:** the rows of every statement of the account are read together, and a row repeated by an overlapping statement counts once only when it is identical, Value included; a corrected value made it a second row of the event.
- **Fix:** upgrade: a row the statements hold with different values is one row; the larger value is kept, whatever the file order, and the ATTENTION line says so on the console (`tjs run --strict` stops on it). Keep only the current statement. On an older release, remove the outdated statement.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/corp_actions.py` — `_ib_union`, `_ib_row_ident`; `src/taxjson/bin/taxjson_run.py` — `attention_lines`

### A merger held in both listings of one security (CAD and USD) in one broker account disposes of only one listing
- **Check:** `tjs events all ACCOUNT` shows the merger's rows for one listing; the other listing's old shares are still in `tjs list`.
- **Cause:** two copies of an event in one broker account with equal quantities were taken for an overlapping statement whatever their listing, so the second listing's copy was dropped (2026-10 audit #105). The shipped extractors give the two listings different event ids, so this needed events built with one id.
- **Fix:** upgrade: a copy on other listings or in another currency is a holding of its own (one election still covers both). On an older release, record the second listing's exchange in a `.tt` file.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/corp_actions.py` — `combine_broker_copies`, `_same_listing`

### A merger held at IB and at a whole-share broker in one taxjson account books whole shares and a short fraction, depending on the order of the files
- **Check:** after the merger, selling the delivered fractional shares (IB delivers e.g. 1.5) leaves a `short` warning for 0.5 share, and the merger rows show cash in lieu IB never paid; renaming the files changes it.
- **Cause:** the combined event kept the first copy's fractional-delivery flag, so when the whole-share broker's copy came first the IB fraction was snapped to whole shares plus phantom cash in lieu (2026-10 audit #106).
- **Fix:** upgrade: the combined event delivers fractions when any copy's broker does. On an older release, keep each broker's exports in its own taxjson account.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/corp_actions.py` — `combine_broker_copies`, `fractional_delivery`

### IB: a partly cancelled order's cost differs from the statement by part of its commission ("… cancels one execution of …")
- **Check:** the statement lists an Order, then a `Ca` row cancelling part of it with no commission refunded (or a refund that is not the pro-rata share); the kept part's cost in `tjs events` differs from the statement by the commission difference.
- **Cause:** the kept part was the order scaled pro rata, commission included, whatever the cancellation itself carried (2026-10 audit #29).
- **Fix:** upgrade: the kept part is the order less the cancellation's own money and commission; a figure the cancellation does not carry is scaled pro rata. On an older release, correct the row in a `.tt` file.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/trade_cancel.py` — `_reduced`

### IB: a `Ca` cancellation pairs with a fill of the same size at another time instead of the order at its own time
- **Check:** the statement holds a fill of the cancelled size at another time and an order at the cancellation's time that it cancels part of; `tjs events` shows the other fill gone and the order whole.
- **Cause:** exact-size fills at any time were taken before a same-time order the cancellation is part of was considered; the price and date match, so only the commission split moves (2026-10 audit #62).
- **Fix:** upgrade: with no exact fill at the cancellation's time and one same-time order it cancels part of, that order is reduced. On an older release, correct the rows in a `.tt` file.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/trade_cancel.py` — `pair_cancellations`

### `tjs slip-audit`: "U1***.2025.dividends.csv line N: date '…' is not a calendar day" (before: a Python traceback, "day is out of range for month")
- **Check:** open the file at that line and look at its ReportDate / ExDate.
- **Cause:** the date's shape was checked (YYYYMMDD) but not the day itself. An impossible day was read as is; on a USD-base report the conversion to CAD then crashed. IB doesn't print such a date: the file was edited.
- **Fix:** download the dividends report again from IB (Reports › Tax › Dividend report) and replace the file in inputs/slips/.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/ib_dividends.py` — `_iso`, `IBReportError`

### `tjs slip-audit`: "U1***.2025.dividends.csv line N: Gross '100' with a blank GrossInBase — the amount in the account's base currency is missing; download the report again"
- **Check:** the GrossInBase cell on that line is empty while Gross is not.
- **Cause:** a blank GrossInBase used to be read as 0, so the payment dropped out of a CAD-base account's T5 / T3 boxes. The comparison then showed a bucket short, blamed on the wrong payment. IB fills GrossInBase on every row: the file was edited.
- **Fix:** download the report again and replace the file. A blank Withhold still reads as 0.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/ib_dividends.py` — `read_report`, `GrossInBase`

### `tjs slip-audit`: "U1***.2025.dividends.csv line N: Withhold '-2' with a blank WithholdInBase — the tax withheld in the account's base currency is missing; download the report again"
- **Check:** the WithholdInBase cell on that line is empty while Withhold is not.
- **Cause:** a blank WithholdInBase was read as 0 tax withheld with no problem said, while the same blank in GrossInBase was refused: the foreign tax paid dropped out of T5 box 16 (2026-10-24 audit B25). IB fills the column on every row: the file was edited or cut.
- **Fix:** download the report again and replace the file. A blank WithholdInBase beside a Withhold of 0 still reads as 0.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/ib_dividends.py` — `read_report`, `WithholdInBase`

### `tjs slip-audit`: "U1***.2025.dividends.csv: the Account section has no BaseCurrency — the currency of GrossInBase and WithholdInBase is unknown; download the report again"
- **Check:** the report's `Account,Header` line has no `BaseCurrency` column, or the `Account,Data` line's cell under it is empty.
- **Cause:** with no base currency stated, the base-currency amounts (`GrossInBase`, `WithholdInBase`) were labelled CAD: a USD-base account's would have gone on a Canadian slip unconverted (2026-10-24 audit B24).
- **Fix:** download the report again with its Account section (Base Currency column) and replace the file.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/ib_dividends.py` — `read_report`, `no BaseCurrency`

### `tjs slip-audit`: "U1***.2025.dividends.csv line N: QZQ: a blank ReportDate — the payment's pay date is missing, so it cannot be placed in a tax year; download the report again"
- **Check:** the ReportDate cell on that line is empty.
- **Cause:** an undated payment was read with no pay date: it was in no year's slip (the report's years left it out) while its amounts counted (2026-10-24 audit B26).
- **Fix:** download the report again and replace the file.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/ib_dividends.py` — `read_report`, `blank ReportDate`

### `tjs slip-audit`: "U1***.2025.dividends.csv line N: Gross '1,5' is not a number this parser accepts"
- **Check:** the cell at that line and column holds a decimal comma or a misplaced thousands comma.
- **Cause:** commas were stripped before reading, so '1,5' was read as 15. Amounts are now read like the T5008 CSVs: thousands commas in groups of three ('1,500') only.
- **Fix:** download the report again (IB writes a decimal point), or correct the cell by hand.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/ib_dividends.py` — `_num`, `parse_strict_number`

### `tjs slip-audit --json`: an IB dividends report source lists "SAMP 2025-02-03: the components add up to 0.00 CAD withheld, the payment to 15.00" (or "… CAD (GrossInBase) …", "… withheld (WithholdInBase) …")
- **Check:** in the report, compare that payment's Summary row with its RevenueComponent rows in the named column.
- **Cause:** only Gross was checked against the Summary row. A report whose components carry less tax withheld, or other base amounts, than the payment passed silently. The base amounts allow a cent per component (IB's rounding).
- **Fix:** download the report again. If IB's own report disagrees, the components (the slip boxes) are what taxjson reads; compare with the T5 / T3 slip.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/ib_dividends.py` — `read_report`, `withheld_base`

## Questrade, RBC Direct and Webull

### Questrade: "Warning: 99900001.csv line 2: DIV row keeps internal symbol code 'X000123' (…)"
- **Check:** the export lists the security under a code (one letter and digits) instead of a ticker, usually for shares transferred in from another broker. `tjs run --details` gives the ticker.map line to add under the warning.
- **Cause:** taxjson resolves a code from the account's own trades and transfers, a matching transfer-out at another broker in the project, or an exact name match (tax-logic CA-ACB-CODES). Nothing resolved this one, so its rows form a pool of their own: a return of capital on it becomes a gain, and the sale of the real ticker goes short.
- **Fix:** add `GLOBAL X000123.TO SAMPH.TO` (the code's listing, then the real ticker) to `ticker.map`; the warning stops.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/questrade.py` — `_resolve_symbol`, `row keeps internal symbol code`; `src/taxjson/lib/symbol_codes.py` — `codes_note`

### Questrade: after a currency journal (Norbert's gambit), "Warning: Short position: G000123.US (rrsp)" or `tjs find-missing-history`: "G000123.US rrsp -300 (2025-09-30)"; or "Warning: 99900001.csv line 3: a BRW journal row (…) has no partner"
- **Check:** the export's `Other` rows of an earlier day hold the two BRW legs `<NAME> JOURNAL POSITION TO USD` (-300, CAD) and `<NAME> JOURNAL POSITION FROM CAD BOOK VALUE: $X CNV@ r` (+300, USD), or TO CAD / FROM USD the other way, and the later sale `<NAME> WE ACTED AS AGENT` is under a code (one letter and digits). On a release with the fix the run says instead "Questrade internal symbol codes resolved" with `G000123 → QZD.U.TO (journal in-leg of 300 on …)` and, in a Canadian project, "joined as one security by their transfer journal: QZD.TO ↔ QZD.U.TO (currency journal …)".
- **Cause:** the website export names both legs by the bare symbol. The USD leg was read as a US listing (QZD.US) and the code of the sale, which no trade or transfer of the account names, stayed a security of its own, so the sale of the journaled units read as a short. Now the parser pairs the legs (one account, one day, one name, the same quantity), books the USD leg on the security's US-dollar line (the account's own USD listing of it, else `SYMBOL.U.TO` by the TSX convention in `src/taxjson/data/markets.toml`), books the code under that line, and a Canadian run joins the two lines as a ticker.map `JOURNAL` line would (tax-logic CA-XLIST-03): the units keep the CAD purchase's ACB. A leg whose partner is missing from the export stays a transfer of its own line, said with both row shapes.
- **Fix:** re-run `tjs run` on a release with the fix. On an older install add `EXTRACT <NAME> | USD | QZD.U.TO` and `JOURNAL QZD.U.TO QZD.TO` to `ticker.map`. For a lone leg, re-export the journal's day so both rows are in the file. A short that remains on the CAD line (QZD.TO) is the units' real purchase missing from your files: supply it (`tjs find-missing-history`).
- **Fixed in:** `v0.23.0`
- **Code:** `src/taxjson/lib/brokerages/questrade.py` — `_plan_qt_journals`, `_journal_listing`, `journal_codes`, `has no partner`; `src/taxjson/lib/cross_listings.py` — `analyze`, `currency journal`; `src/taxjson/lib/markets.py` — `usd_unit_listing`

### "Warning: 99900001.csv: Questrade symbol QZOLD.US looks renamed to QZNEW.US" or "Warning: 99900001.csv: RBC symbol QZOLD (USD) looks renamed to QZNEW"
- **Check:** `tjs run --details` says under it that the old symbol stops with shares still open and the new one (same description) starts with a sale or goes short; `tjs renames --pending` lists it as suggested with the `.tt` line `RENAME <date> OLD NEW` and the dated ticker.map line that book it. IB says `IB lists one stock (contract id …) under several symbols`; Webull says `… goes short with a SALE on … — likely a ticker change Webull reported without a reorganization row`. `tjs shares` shows the old symbol open and the new one short.
- **Cause:** the broker changed the ticker without a reorganization row. As exported, the old pool is stranded and the new symbol's sale reads as a short, so its gain is in no total.
- **Fix:** if they are one security, add the line the warning gives to a `.tt` file of the account: the ticker change as a dated event, date first (`RENAME 2025-05-10 QZOLD.US QZNEW.US`, the new symbol's first row; use the broker's change date if you know it), see `tjs renames`. The hint stops once a `.tt` RENAME line or a ticker.map rule joins them. Earlier releases suggested a ticker.map line (`GLOBAL QZOLD.US QZNEW.US`, or a dated `RENAME QZOLD.US QZNEW.US 2025-05-10`), still read. IB's one contract id under two symbols is booked without a line (next entry).
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/brokerages/questrade.py` — `looks renamed to`; `src/taxjson/lib/brokerages/rbc_direct.py` — `looks renamed to`; `src/taxjson/lib/brokerages/ib_extractor.py` — `under several symbols`; `src/taxjson/lib/brokerages/webull.py` — `ticker change Webull reported without a`

### "Warning: the account's IB statements: IB lists one stock (contract id …) under several symbols: QZOLD, QZNEW — booked as a ticker change, a dated event (QZOLD.US -> QZNEW.US on 2025-05-12)"
- **Check:** the default console shows the line up to " — " (cut at the terminal's width), as for each entry below that starts the same way; `tjs run --details` shows the rest, which tells them apart. `tjs renames` lists the change with source `IB contract id` and the position and cost it carried. If the two symbols are two different securities, `tjs shares` would show QZOLD's rows merged into QZNEW. A trade in QZOLD after the date is listed by `tjs renames` (another company reusing the ticker, or IB's late rows).
- **Cause:** IB changed the ticker of one contract id without a corporate-action row. One contract id is strong evidence: the parser books the change as a dated rename event (event_source `ib-conid`, at 00:00 on QZNEW's earliest row of any section — a trade, a transfer, a corporate action, a dividend, a return of capital): the position, its cost and acquisition dates carry to the new symbol, no ticker.map line needed. It does so only when that contract id's own rows date the change (QZOLD's last row that moves a position or its cost — a trade, a transfer, a corporate action, a return of capital — on an earlier day; a dividend, withholding or payment in lieu of QZOLD paid after the change is income and does not count), read from every IB account of the project: one decision and one date in all of them; otherwise see the next entry. A `.tt` RENAME line for the change, or a ticker.map rule joining the two in that direction, books it instead (no Warning); a corporate action the parse books as the change (IB's own renaming split, a merger) books it from that row. Before the second pre-release review each IB account decided alone (one account booked the change while another refused it, and that account's hint date could move the first account's change past its sale), and a QZOLD dividend or withholding adjustment after QZNEW's first row refused the change.
- **Fix:** nothing when they are one security (earlier releases asked for the ticker.map line `RENAME QZOLD.US QZNEW.US 2025-05-12`, which still works). If they are two securities, add `DISTINCT QZOLD.US QZNEW.US` to ticker.map; if QZOLD's rows after the date are another company's, add `RENAME 2025-05-12 QZOLD.US QZNEW.US late=separate` to a `.tt` file of the account (`late=fold`: IB's late rows are the renamed shares). Then `tjs run`.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `_warn_stock_aliases`, `_book_conid_renames`, `_merge_seen`, `_CONID_MOVES`, `_ib_account_prescans`, `booked as a ticker change`; `src/taxjson/bin/taxjson_run.py` — `stage_ib_project`; `src/taxjson/bin/taxjson_brokerage.py` — `--project-statements`; `src/taxjson/lib/renames.py` — `row_source`, `SOURCE_IB_CONID`

### "Warning: the account's IB statements: IB lists one stock (contract id …) under several symbols: QZOLD, QZNEW — a ticker change taxjson does not book from the contract id: …"
- **Check:** the warning's detail (`tjs run --details`) says why and gives the `.tt` line, e.g. "QZNEW's rows begin on 2025-05-12 (a trade or transfer in account 'ma'), on or before QZOLD's last row that moves its position or cost (2025-05-20, a return of capital row in account 'mb') … `RENAME 2025-05-12 QZOLD.US QZNEW.US`" (the date is QZNEW's earliest row in any IB account; every IB account holding the stock says the same), or "ib.csv lists QZNEW under several contract ids (…): the rows there cannot be told apart". `tjs renames` lists the change as suggested; QZNEW's sale goes short until the change is booked (`run --strict` stops). On v0.24.0 the change was booked on QZNEW's first trade (a split or return of capital of QZNEW before that trade was lost; now it is dated at QZNEW's earliest row of any section) and booked even then: a ticker another company had used dated the change from that company's rows, and an IB split that also renames was booked twice (phantom shares).
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

### "Warning: the account's IB statements: IB lists one stock (contract id …) under several symbols: QZOLD, QZNEW — inputs/margin/renames.tt:1 declares the ticker change the other way round (QZNEW.US -> QZOLD.US) …"
- **Check:** the rest of the line (`tjs run --details`) says how the contract id's rows run (QZOLD's rows end before QZNEW's begin) and the line to write instead; `run --strict` stops.
- **Cause:** a `.tt` RENAME line (or a ticker.map dated RENAME) declares the change from the newer symbol to the older one while the rows date it the other way. Before the second pre-release review a line in either direction silenced the contract id, and the backwards line moved nothing the right way.
- **Fix:** if QZOLD became QZNEW, write the line as the message gives it (`RENAME 2025-05-12 QZOLD.US QZNEW.US`); if they are two securities, replace it with `DISTINCT QZOLD.US QZNEW.US` in ticker.map. Then `tjs run`.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `_declared_join`, `the ticker change the other way round`

### "Warning: the account's IB statements: IB lists one stock (contract id …) under several symbols: QZOLD, QZNEW — a ticker change"
- **Check:** the rest of the warning (`tjs run --details`) names the dated line to add (a statement parsed on its own, or a date missing), e.g. `RENAME 2025-05-12 QZOLD.US QZNEW.US`; `tjs shares` shows QZOLD open and QZNEW short. On v0.22.0 and earlier it suggested an undated `GLOBAL` line, ordered by first trade, and could point the real ticker at IB's temporary symbol (a time stamp YYYYMMDDHHMMSS before the ticker, given around a corporate action): `GLOBAL QZX.US <stamp>QZX.US`. Never add that line.
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

### "Warning: possible superficial loss across listings: QZX.TO sold at a loss, QZX.US bought within 30 days" (US: "possible wash sale across listings")
- **Check:** `tjs run --details` names under it the loss, the purchase and the name both listings share, then the two ticker.map lines that answer it; `tjs ticker-map --suggest` offers the `TOBASE` line, and `tjs run --strict` stops on it. The loss is still allowed in `tjs sum`.
- **Cause:** two listings of one company's shares (the TSX line and the NYSE line) are identical property, but taxjson joins them only on evidence: a ticker.map `TOBASE` line, a `.tt` JOURNAL line or a transfer journal it pairs itself (tax-logic CA-XLIST-01), never on the names alone. Sold at a loss on one listing and bought on the other within 30 days, the unjoined pair kept the loss allowed with nothing said: no warning, no suggestion. Now a loss in the tax year with another listing of the same root bought within 30 days before or after it, in any of your accounts (registered included), under a name equal word for word once normalised, is flagged (CA-XLIST-05; US-XLIST-04 for a US project's wash sales). In Canada the other listing must still be held at the end of day 30, as the rule requires; the US rule has no such test. Names that differ, or a listing whose exports carry no name (a `.tt`-only book), are not flagged. A class share's root is also read without its class letter (a loss on `QZT.B.TO` and a purchase of `QZT`, both named "… CL B"); a split or consolidation of the other listing scales the units held at day 30. The run lists the first 20 pairs and counts the rest in one line ("3 more possible superficial losses across listings, not listed here"); `tjs ticker-map --suggest` lists every one.
- **Fix:** if the two listings are one security, add the `TOBASE` line (`tjs ticker-map --suggest --write` offers it) and `tjs run`: the loss is denied (disallowed) and its amount goes onto the replacement's cost. If they are different securities (a CDR, another company that uses the root), add the `DISTINCT` line. Either line ends the warning.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/xlist_loss_radar.py` — `analyze`, `open_findings`, `message`, `across listings`, `_roots`, `_held_at`, `RADAR_SHOWN`, `more_message`; `src/taxjson/bin/taxjson_run.py` — `_say_xlist_losses`; `src/taxjson/lib/ticker_map_suggest.py` — `from_xlist_loss_radar`

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

### "Warning: acct: ticker.map books the two legs of a transfer as two securities: 24 QZB.US out 2026-09-01 (booked as QZZ.TO) / QZA.US in 2026-09-03"
- **Check:** `tjs journals` lists the pair as refused by your ticker.map; the line naming QZB.US (or QZA.US) books it as a symbol the other leg is not booked as.
- **Cause:** the two legs pair as one move (the same quantity, within 5 business days), but the map's lines book them as two different symbols, so neither leg pairs: the units leave one security and arrive in another (in a registered account a withdrawal at fair value; the in-leg's position has no cost carried). Earlier releases said nothing.
- **Fix:** if they are one security, add the line the Warning names (`TOBASE QZA.US QZZ.TO`) or correct the line naming the other leg; if they are two securities, add `DISTINCT QZB.US QZA.US`. `tjs run --strict` stops until one of them is in ticker.map. The Warning is only for legs whose names agree, and not for a pair a `DISTINCT` line keeps apart (the legs or the symbols they are booked as): a development build after v0.24.0 also warned (and `--strict` stopped) on an in-leg with no name that only shared a quantity and a date with an unrelated transfer.
- **Fixed in:** `v0.24.1`
- **Code:** `src/taxjson/lib/cross_listings.py` — `map_split`, `map_split_note`, `books the two legs of a transfer as two`; `src/taxjson/bin/taxjson_run.py` — `stage_cross_listings`

### Questrade: "Warning: 99900001.csv: 1 dividend(s) marked NON-RES TAX WITHHELD are booked at the NET amount"
- **Check:** the rest of the line (`tjs run --details`) lists each dividend (`QZQ.US 2025-03-15 8.50`); `tjs divs` shows it at the net amount with no TAX row. On RBC the same wording is grossed up instead: `tjs events` shows a TAX row described `(Implied Tax)` at 15% of the gross.
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

### RBC: "Warning: rbc_2024.csv: reorganization on 2024-05-15: the removal is booked under RBC temporary code X000006 ('YYK INC') and no row in this account's RBC files names that company's ticker, so it is ASSUMED to be the receipt's YYL.TO" although the same export buys YYK; then "1 position sold in 2024 with no purchase in your files ... YYL.TO"
- **Check:** `head -5` of the RBC export: a header without a `Symbol Description` column (`Date,Activity,Symbol,Description,…`, the layout of `examples/rbc_direct_demo.csv`), and a `Buy` row whose Description names the company (`YYK INC - Buy`). `tjs renames` lists no `YYK.TO -> YYL.TO`, `tjs list` shows YYK.TO still held and YYL.TO short ("missing history?"), and `tjs sum` leaves the sale out of the totals.
- **Cause:** RBC's company-name lookups (a name change's removal under a temporary code, a trade under a temporary code, a ticker change without a reorganization row, a spin-off's cash in lieu) read the optional `Symbol Description` column only, so with no such column nothing matched: the name change became a self-rename to the receipt's ticker, the old pool was stranded and the sale went short. They now all read the trade's own Description (less ` - Buy` / ` - Sell`) when the column is missing, as the spin-off parent lookup (entry above) already did.
- **Fix:** upgrade and run again: the rename is booked and the sale is in the totals at the pool's cost. On an older release, add the line the warning names to `ticker.map` (`GLOBAL YYK.TO YYL.TO`).
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/lib/corp_actions.py` — `rbc_row_company`; `src/taxjson/lib/brokerages/rbc_direct.py` — `build_rbc_account_context`, `_resolve_temp`, `_emit_stock_reorg`, `_detect_ticker_changes`, `same security`

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

### `tjs fetch`: "Questrade auth failed: request failed: HTTP 400 Bad request — invalid request https://login.questrade.com/oauth2/token?<query hidden>" (or an account shown as `99***` in an error's body)
- **Check:** the error ends with what the broker (or a proxy in between) answered. On v0.29.0 and older that answer was shown as it came, so a server that echoed the request URL showed the refresh token (or the Flex token), and an account number in a JSON message was shown in full beside the masked request path (issue #81).
- **Cause:** the request path was masked, but the HTTP reason and body were appended unchanged. Now every error text from a broker (reason, body, a Flex ErrorMessage or the start of a body that is not XML, a connection error) has the request's tokens and account numbers replaced by `***`, URL query strings hidden, `token=` / `"refresh_token":` values, Bearer credentials and account-shaped ids masked, before it is cut to length.
- **Fix:** upgrade. The rest of the message (status, error code, the broker's text) is kept, so the cause can still be read. If a token was printed by an older release, start a new token chain (`tjs fetch --refresh-token <new token>`).
- **Fixed in:** `v0.29.1`
- **Code:** `packages/taxjson-fetch/src/taxjson_fetch/api.py` — `scrub_detail`, `_http_error_detail`, `_flex_err`, `qt_refresh`, `_qt_get`, `flex_fetch`

### `tjs fx-cash --ledger v2` reports a gain (or the wrong year-end cost) for a Questrade account with two currency conversions on one day, with the reconciliation OK
- **Check:** the Questrade export lists rows newest first (the web export's order), and one day has two FXT conversions, or a conversion and a deposit or withdrawal; `tjs fx-cash --ledger v2 --json` shows that day's `FXCONV` events in reverse order.
- **Cause:** the v2 cash reader read the rows in file order and paired each leg with the first leg of another currency that day, so a newest-first file replayed same-day conversions backward; the books' parser already read such a file bottom-up (2026-10 audit #75).
- **Fix:** upgrade: the reader puts rows in real order as the parser does and pairs the two adjacent legs of each conversion. On an older release, declare the day's conversions as `FXCONV` lines in a `.tt`.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/brokerages/questrade.py` — `questrade_cash_events`, `_qt_real_order`

### `tjs opening ACCOUNT holdings/<account>_live_holdings.toml` (the `tjs fetch` snapshot) skips every holding: "no currency for its cost"
- **Check:** the fetched holdings TOML has no `currency =` line under each `[[holding]]`.
- **Cause:** Questrade's positions payload carries no currency, and the fetch plugin wrote none, though settings.md requires one per holding; `tjs sanity` also compared a USD average with CAD books (2026-10 audit #43).
- **Fix:** upgrade (taxjson-fetch): each holding's currency comes from its listing's suffix. On an older release, add `currency = "CAD"` or `"USD"` to each holding.
- **Fixed in:** `v0.31.0`
- **Code:** `packages/taxjson-fetch/src/taxjson_fetch/api.py` — `positions_to_holdings_toml`

### `tjs fetch`: `AttributeError: 'list' object has no attribute 'get'`
- **Check:** the traceback ends in `qt_refresh` or `qt_positions` / `qt_activities`.
- **Cause:** Questrade's token or API endpoint (or a proxy in between) returned JSON that is not an object; the fetcher read fields from it unchecked.
- **Fix:** upgrade; the reply is now the one-line error "unexpected response from …: a JSON list, not an object". Retry later or check the proxy.
- **Fixed in:** `v0.31.1`
- **Code:** `packages/taxjson-fetch/src/taxjson_fetch/api.py` — `_http_json`, `_qt_get`, `qt_refresh`

### `tjs fetch` (Questrade): one of two identical same-day fills is missing from `questrade_<year>.csv`; `tjs sanity` shows the position short by that fill
- **Check:** the manual activity export shows two byte-identical rows (same date, symbol, action, quantity, price) and the fetched file holds one; the trade date is the last day of one of the fetch's chunks of at most 28 days, counted from the window's start (Dec 1 of the prior year, or `--from`).
- **Cause:** the API returns a boundary day's activities in both adjacent chunks, so the fetch drops rows an earlier chunk already returned. It kept a set of them: when the first chunk held one copy and the next held two, both copies in the second chunk were dropped, though the second was a fill the first chunk never saw (2026-10-24 audit A21).
- **Fix:** upgrade (taxjson-fetch) and re-fetch: a row is a repeat only up to the number of copies the earlier chunk held, as the merge into the existing file already counts. On an older release, add the missing fill from the manual export as a `.tt` line.
- **Fixed in:** `v0.35.0`
- **Code:** `packages/taxjson-fetch/src/taxjson_fetch/api.py` — `qt_activities`, `seen_prev`

### `tjs fetch`: "Error: … unexpected response from /v1/accounts/99***/activities: no 'activities' list (keys: code, message; …) — the window was not downloaded; nothing written, nothing trimmed"
- **Check:** the message shows what the broker (or a proxy in between) answered at HTTP 200 instead of the window's activities: its keys, and its `code` / `message` when it has them.
- **Cause:** Questrade's activities endpoint answered with a JSON object that has no `activities` list (an error passed through at HTTP 200, a changed payload, `activities: null`). Before, that read as a successful EMPTY window: the fetch added nothing and said so, and with `--trim-overlap` the manual export's rows inside the window were deleted (kept in its `.bak`) as if the download now held them (2026-10-24 audit B01).
- **Fix:** retry later, or check the proxy; nothing was written or trimmed. On an older release, a fetch that reported `+0 new row(s)` with `0 downloaded` for a window that had activity is this: restore the trimmed export from its `.bak` and re-fetch.
- **Fixed in:** `v0.35.0`
- **Code:** `packages/taxjson-fetch/src/taxjson_fetch/api.py` — `_qt_activity_list`, `_qt_response_list`, `qt_positions`

### `tjs fetch` (IBKR Flex): "Error: ib: the Flex download contains 3 account statements — a multi-account query would blend books that must stay separate. Scope the Flex query to ONE account …" for a query that covers one account
- **Check:** the download's first lines: one `Statement,Header` row and several `Statement,Data` rows (BrokerName, Title, Period, WhenGenerated). The count in the message is the number of those `Data` rows.
- **Cause:** the guard against a multi-account query counted the `Statement,Data` rows, not the statements, so a normal download with more than one metadata row (every download with section codes and line descriptors enabled) was refused; nothing was written (2026-10-24 audit B02).
- **Fix:** upgrade (taxjson-fetch): the guard counts the runs of `Statement` rows, one per concatenated account statement. On an older release, download the statement by hand into `inputs/<account>/`.
- **Fixed in:** `v0.35.0`
- **Code:** `packages/taxjson-fetch/src/taxjson_fetch/api.py` — `flex_statement_count`; `packages/taxjson-fetch/src/taxjson_fetch/command.py` — `account statements`

### `tjs fetch --trim-overlap` (Questrade): "manual.csv: trimmed 3 row(s) inside the fetched window" removed rows of another Questrade account kept in the same folder; or "Warning: margin/manual.csv: no 'Account #' column — cannot tell which rows are account #99***'s; not trimmed"
- **Check:** the trimmed export's `.bak` (`manual.csv.bak`, `.bak2`, …) holds rows inside the window whose `Account #` is not the fetched account's.
- **Cause:** the overlap count and the trim selected a sibling export's rows by trade date alone, so with a second Questrade account's export in the folder (`[accounts.<name>] account` names one account; the parser books every row of the folder to it), fetching one account removed the other's rows from the active file (2026-10-24 audit B28). Now a row is trimmed, and warned about, only when its `Account #` is the fetched account's (a blank cell counts as its own: the parser books such rows to the folder's account, so they would double-count against the fetched file). A sibling with no `Account #` column at all is refused, not trimmed by date.
- **Fix:** upgrade (taxjson-fetch). To recover rows an older release trimmed, copy the other account's rows back from the `.bak` (or move the whole `.bak` back over the file and re-run `tjs fetch --trim-overlap`). A second Questrade account belongs in its own taxjson account folder.
- **Fixed in:** `v0.35.0`
- **Code:** `packages/taxjson-fetch/src/taxjson_fetch/command.py` — `_qt_account_col`, `_row_of_account`, `_qt_window_overlap`, `_qt_trim_file`

### `tjs sanity` / `tjs opening`: "Error: holdings.csv: cost column 'Cost Basis' is neither a total (Book Cost, Book Value) nor a per-share figure (Average Cost, Book Cost Per Share) — refusing to guess which; rename the column to one of these"; on an older release a 'Book Cost Per Share' column was read as the position's total cost
- **Check:** the header row of the RBC Holdings Export: the cost column's label.
- **Cause:** columns are matched by label. A per-share label that also names a book cost ('Book Cost Per Share', 'Average Book Cost', 'Book Cost/Share') matched the total pattern first, so the opening cost was the per-share figure. A cost label that says neither total nor per share is now refused rather than guessed.
- **Fix:** upgrade. If refused, rename the column to 'Book Cost' / 'Book Value' (a total) or 'Average Cost' / 'Book Cost Per Share' (per share), then run again.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/positions_reports.py` — `_read_rbc`, `_RBC_PER_SHARE_RE`, `_rbc_cost_label_error`

### RBC: "Warning: cash-in-lieu row with no reorganization to fold into — line 4: … CIL - ZZS SPINCO INC CASH IN LIEU OF FRAC SHARES. The cash was NOT booked; add the fractional disposition by hand."
- **Check:** the warning names a CIL row that no merger, split or name change of the account explains. If a spin-off (`DIS - … SPINOFF ON …`) of that security sits within 3 days before to 45 days after it, it is the spin-off's fraction: a current release pairs it with the spin-off and no longer prints this warning (next entry). Otherwise the reorganization's other rows are in an export you have not added, or the row belongs to an event taxjson does not read.
- **Cause:** RBC pays cash for the fraction a reorganization ratio leaves; taxjson books it with the event it pairs it to (same account; the event's ticker or its full company name; paid 3 days before to 45 days after). A row it cannot pair is not booked.
- **Fix:** add the export holding the event's rows; if the event is not one taxjson reads, book the fraction's sale in a `.tt` file (its $0 acquisition and its sale for the cash, dated on or after the event).
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/corp_actions.py` — `pair_rbc_reorganizations`; `src/taxjson/lib/brokerages/rbc_direct.py` — `cash-in-lieu row with no reorganization`

### Questrade or RBC: an s.86.1 spin-off's fraction paid in cash is sold at $0 cost, or "NOT booked"
- **Check:** the spin-off delivered whole shares and a cash-in-lieu row paid the fraction (Questrade `CIL … CASH IN LIEU OF .5`, RBC `CIL - … CASH IN LIEU OF …`), and you elected `rollover_s_86_1`. In `tjs events all ACCOUNT`, an older release shows the fraction bought at $0 and sold (Questrade; its cost depends on the order of the rows), or RBC's warning above with no sale at all. A current release books the spun-off shares at the whole and fractional quantity with the full allocated cost, the parent's cost reduced by all of it, and one sale of the fraction for the cash (`tax-logic` CA-CORP-05).
- **Cause:** the rollover gave the whole shares the allocated cost and never booked the fraction's disposition; Questrade's own pair sold it at $0 when its row came before the spin-off's, and RBC's row was not booked.
- **Fix:** upgrade and re-run `tjs run`: the fraction's gain is the cash less its share of the allocated cost. When an RBC row does not state the fraction (`CASH IN LIEU OF FRAC SHARES`), the run says `warning: UNBOOKED: spin-off … cash in lieu of a fractional share is NOT booked`: add a `.tt` BUY of the fraction at 0 and its SELL for the cash, dated on or after the spin-off.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/corp_actions.py` — `_allocated_spinoff_selling_fraction`, `drop_broker_cil_pairs`, `warn_unsold_spinoff_cil`, `_qt_spinoff_cil`, `rbc_spinoff_cil_fraction`

### "Warning: UNBOOKED: spin-off ZZP.TO→ZZS.TO … cash in lieu of a fractional share is NOT booked" on every run after you added the fraction's BUY and SELL in a `.tt` file; `tjs run --strict` stops on it
- **Check:** `tjs gains` already lists the fraction's sale (0.5 ZZS.TO for the cash) from your `.tt` file.
- **Cause:** the warning looked only at the rows the election books, never at the account's `.tt` files, so the fix it names could not end it. The corporate-action stage now reads the `.tt` sales: a sale of less than one share of the spun-off symbol, dated from the spin-off to 50 days after the cash was paid (the warning names the last day), is the cash booked by hand: no warning, a note in the stage's .diag.
- **Fix:** upgrade and run again. A sale dated outside those days still warns: date it within them.
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/lib/corp_actions.py` — `read_tt_sales`, `_tt_fraction_sale`, `warn_unsold_spinoff_cil`; `src/taxjson/bin/taxjson_corp_actions.py` — `--tt`; `src/taxjson/bin/taxjson_run.py` — `_corp_tts`

### RBC: "Warning: RBC spin-off parent 'ZZP PARENT CORP' (SEC# X123456) is not traded in this statement, so its ticker is unknown" although the export buys the parent; after an s.86.1 election, a gain on the bare SEC# code and the parent keeping all its cost
- **Check:** `head -5` of the RBC export: a header without a `Symbol Description` column (`Date,Activity,Symbol,Description,…`, the layout of `examples/rbc_direct_demo.csv`). `tjs gains` lists a 0-unit row for the SEC# code (`X123456`) with a negative cost, and `tjs list` shows the parent at its full cost.
- **Cause:** the spin-off's parent was looked up by company name in the `Symbol Description` column only, which RBC exports do not always have; the trade row's own Description was never read. Unfound, the parent was the bare SEC# code, and the s.86.1 cost split landed on that empty pool as a gain. The parent is now found from the trade's Description (`ZZP PARENT CORP - Buy`) too.
- **Fix:** upgrade and run again; the election id changes (it names the parent's ticker), so set the election again: `tjs elect --pending`.
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/lib/corp_actions.py` — `rbc_row_company`, `is not traded in this statement`

## Crypto

### `tjs checklist`: "! 16 crypto-sends (blocked): no crypto transfer evidence in work/ — run `taxjson run`" and "! 24 option-boundary (blocked): no books for crypto — run `taxjson run`"; `tjs option-boundary`: "Warning: no work/crypto_base.json — `taxjson run`", and running it changes nothing
- **Check:** `tjs run` says "no CSVs or .tt files in inputs/crypto; skipping account 'crypto'": the `[accounts.crypto]` table `tjs init` wrote is still there with nothing in its folder.
- **Cause:** the run skips an account whose inputs folder is empty, but option-boundary counted the crypto account (which writes no options) and any skipped account as a missing book, and the checklist's crypto-sends step asked for a run. Now option-boundary leaves out crypto and empty accounts (with the only taxable account empty it says "NOT CHECKED — inputs/margin is empty"), and the checklist says "inputs/crypto is empty: add exports or delete [accounts.crypto]".
- **Fix:** save the exchange's exports in inputs/crypto, or, with no crypto, delete `[accounts.crypto]` from taxjson.toml (docs/getting-started.md step 2).
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/lib/checklist.py` — `d_crypto_sends`, `add exports or delete`; `src/taxjson/bin/taxjson_run.py` — `cmd_option_boundary`, `_accounts_skipped_for_no_inputs`, `so the run made no books`

### Canada: a coin held below a millionth of a unit (0.0000009 QZCOIN) — Schedule 3 shows a gain equal to a loss that `tjs run --details` says was denied (superficial), and `tjs t1135` lists no property for it although the inventory holds it with its cost
- **Check:** `tjs run --details` names the sale's loss as denied and the repurchase as its replacement; the replacement's later sale shows a gain of that amount with the original cost only. `tjs t1135 --json`: `max_total_cost` 0 and no `per_symbol` row for the coin while `tjs run`'s inventory holds the units and their cost.
- **Cause:** the denied loss's cost adjustment and the T1135 cost walk tested the position against the 1e-6 share tolerance, which reads a sub-micro coin position as empty: the adjustment was parked for "the next opening" and the closing sale never recovered it; the T1135 walk valued the holding at nothing. The trades themselves already used the coin tolerance (float noise only).
- **Fix:** upgrade: the adjustment landing, the parked-deferral recovery on a rename and the T1135 walk use the coin tolerance. Then `tjs run`.
- **Fixed in:** `v0.30.4`
- **Code:** `src/taxjson/lib/core.py` — `_aeps`, `pool_qty_eps`, `pending_wash`; `src/taxjson/bin/taxjson_t1135.py` — `eps`, `cost_amount`

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

### "Warning: UNBOOKED: Kraken trades kraken_demo.csv: 1 row(s) whose type is neither buy nor sell (settle x1) are NOT in the books" ending "(the file is refused until it is gone)", but the run finishes (exit 0)
- **Check:** `tjs run --details` shows the whole warning; `tjs trades` and `tjs sum` have the file's other trades, without that row.
- **Cause:** the row is left out and the rest of the file is booked; only `tjs run --strict` stops on an UNBOOKED line. The clause about the file being refused was copied from the parser's refusals, which do stop the file. The warning no longer has it.
- **Fix:** correct the row's type cell (`buy` or `sell`) and `tjs run`. Or enter the fill as a `.tt` line and remove the row from the export: while the row is there, the warning stays and `--strict` stops on it.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/brokerages/kraken.py` — `_parse_trades`, `whose type is`

### "Error: kraken_demo.csv: Kraken trades CSV kraken_demo.csv line 7: the row has 1 cells but the header has 13" for a line holding only spaces (often the last line)
- **Check:** open the export in a text editor: the line named holds only spaces or tabs. An empty line was already skipped, and the same line in a Questrade or Coinbase export is ignored.
- **Cause:** the Kraken reader refuses a row with fewer cells than the header (a missing fee would read as 0), and a line of spaces read as a row of one cell.
- **Fix:** upgrade: a line holding only spaces or tabs is skipped like an empty one. A real short row is still refused, now worded "kraken_demo.csv line 7: the Kraken trades row has 2 cells but the header has 13". On an older release, delete the line.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/brokerages/kraken.py` — `_dict_rows`, `_check_row_width`

### "Error: kraken_demo.csv: Kraken ledger CSV kraken_ledger.csv: required column(s) missing: fee." (the trades file named first)
- **Check:** the folder holds a Kraken trades export and a ledgers export, and the ledger's header (listed after `Header:`) lacks the column named.
- **Cause:** a trades export reads the Kraken ledgers beside it (for the fee currency), so the ledger's refusal came while the trades file was parsed and was prefixed with the trades file's name. It now starts with the ledger's: "Error: kraken_ledger.csv: Kraken ledger CSV: required column(s) missing: fee."
- **Fix:** download the ledgers export again with every column (here `fee`) and `tjs run`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/brokerages/kraken.py` — `_require_columns`, `_sibling_ledger_index`; `src/taxjson/bin/taxjson_brokerage.py` — `_names_its_file`

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

### "taxjson-fill-crypto: warning: ~/.crypto_price_cache.json: entry QZCOIN-2025-04-23 is 0, not a price — ignored and looked up again"; on an older release the row was "left UNPRICED" on every run, with no "failed to fetch" line, although Yahoo was reachable
- **Check:** `work/crypto_filled.json.diag` (and the `.sum` DIAGNOSTICS) holds the line. Before the fix the same coin and date were in "row(s) left UNPRICED" run after run and nothing was fetched for them; the cache file has `"QZCOIN-2025-04-23": 0` (or a negative).
- **Cause:** a cached 0 (a failed lookup an older release wrote, or a hand edit) was never applied as a price, but its key stopped the lookup, so the row stayed unpriced until the cache was edited by hand. A 0, a negative or a non-number entry is now a miss: dropped with this warning and looked up again, and a successful price replaces it.
- **Fix:** none needed: re-run `tjs run` online. To clear such entries by hand, delete the line from `~/.crypto_price_cache.json`.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/fill_crypto_prices.py` — `_damaged_entry`, `cached_price`, `not a price — ignored`

### A coin swapped for a stablecoin (1 QZCOIN for 10 USDC, both legs unpriced in the export) books the coin's proceeds at Yahoo's close of the coin (8) while the USDC costs 10; `taxjson-fill-crypto`'s "crypto-to-crypto swap(s) valued ONCE" note does not count it
- **Check:** the two rows share one id stem (Coinbase Convert `<id>-sell` / `<id>-buy`, Kraken `<refid>-sell` / `-buy` or `<txid>-base` / `-quote`) and neither carries a price or total; after the fill the stablecoin leg's price is 1.00 and the coin leg's is the daily close, so the books show a gain or loss of the difference that no trade made. A US project (stablecoins are property, US-CRYPTO-02) or a hand-entered pair: a Canada project folds stablecoins to US-dollar cash in the parsers (CA-CRYPTO-02), so its exchange rows do not reach this.
- **Cause:** swap pairing left stablecoin legs out, so the coin leg took its own daily close while the stablecoin leg was priced at par: one exchange, two values. A stablecoin leg now pairs like any coin's, and the pair takes the stablecoin's 1.00 USD par on both legs (the spent coin's proceeds are the stablecoins received; a coin bought with stablecoins costs the stablecoins spent), as `taxjson tax-logic` US-CRYPTO-02 states.
- **Fix:** upgrade and `tjs run`.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/fill_crypto_prices.py` — `_swap_pairs`, `_value_swaps_once`, `valued ONCE`

### "Warning: UNBOOKED: Coinbase coinbase_demo.csv: 1 row(s) of type(s) the parser does not book (Airdrop x1; 2025-05-02..2025-05-02; assets QZQC) move coins"
- **Check:** `tjs run --details` shows the rest of the line: "… They are NOT in the books: enter each via a .tt file." `tjs run --strict` stops on it.
- **Cause:** the row's Transaction Type is one the Coinbase parser does not know (an airdrop, a new reward label). taxjson does not guess what it means for tax, but the coins moved.
- **Fix:** enter each row as a `.tt` line in the account folder (an airdrop or reward: income and a purchase at fair value; a payment: a sale).
- **Fixed in:** —
- **Code:** `src/taxjson/lib/brokerages/coinbase.py` — `the parser does not book`

### "Warning: Short position: ETH (coins): its purchase is missing — `taxjson find-missing-history`" for a coin you bought twice in one second, with a Coinbase file whose ID cells are blank (or a Kraken file whose `txid` cells are blank)
- **Check:** the export has an `ID` column (Kraken: `txid`) but the cell is empty on the rows in question, and two of them are alike in every other cell (same second, asset, quantity, price and total). `tjs sum` books fewer units and a smaller gain than the export holds; `tjs sanity` against a holdings file shows the short. On v0.29.2 and older (issue #93).
- **Cause:** taxjson keeps two identical rows of one file apart by the broker's row id. A row with no id is told apart by its order among identical rows (as rows of an older Coinbase layout with no ID column always were), but a blank cell in an ID column took neither path, so the two purchases became one row and the sale went short.
- **Fix:** upgrade and `tjs run`: rows without an id are now kept apart, while the same rows in an overlapping export are still read once. On an older release, delete the empty ID column (or fill in distinct ids).
- **Fixed in:** `v0.30.0`
- **Code:** `src/taxjson/lib/brokerages/coinbase.py` — `parse_file`, `_content_stem`; `src/taxjson/lib/brokerages/kraken.py` — `_mark_idless_fills`; `src/taxjson/lib/brokerages/base.py` — `content_stem`, `disambiguate_split_fills`

### "Error: cb.csv: Coinbase CSV cb.csv lines 2 and 3: ID X1*** is on two rows with DIFFERENT content (…)" (Kraken: "Kraken trades CSV … txid TQ*** is on two rows …"); or, on an older release, one of two trades that share an ID missing from the books with no message
- **Check:** the two lines the message names carry one `ID` (Kraken trades: `txid`) but differ in date, quantity, price or another cell. On an older release: `tjs list` shows fewer coins than the export bought, `tjs sanity` against a holdings file shows the short, and nothing in `tjs run` said why.
- **Cause:** the Coinbase and Kraken parsers keep the broker's own row id as the transaction id, and the de-duplication reads one id twice in one file as the file repeating itself (two downloads pasted together) and books one row. Two rows that differ are not a repeat, so the second trade was dropped with no message (2026-10-25 audit robustness#4). They are refused now; an identical repeat is still booked once, and the legs one row books (a convert's sale and purchase) carry ids of their own. The Kraken ledger already refused a ledger `txid` repeated with different content.
- **Fix:** look at both lines in the export. Delete the one that is a stray or edited copy, or download the export again. If the broker's own export holds both trades, give one a distinct id (append `-2` to its ID cell) and `tjs run`.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/brokerages/_crypto_common.py` — `RowIds`, `DIFFERENT content`; `src/taxjson/lib/brokerages/coinbase.py` — `parse_file`; `src/taxjson/lib/brokerages/kraken.py` — `_parse_trades`; `src/taxjson/bin/taxjson_sort.py` — `plan_dedup`

### "Error: cb_a.csv and cb_b.csv: row id CB*** is in both with DIFFERENT content (cb_a.csv: 2024-10-08 BUYSELL BTC -0.05 USD 3075.20; cb_b.csv: …)"; or, on an older release, the row of one of two overlapping downloads kept with no message
- **Check:** the two files are downloads of one Coinbase (or Kraken) account that overlap, and the row with that ID (Kraken: `txid`) differs between them: its price, amount, quantity, fee or date. On an older release `tjs run` said nothing and `work/<account>_sorted.json` held the version of the file whose name sorts first.
- **Cause:** the Coinbase and Kraken parsers keep the broker's own row id, and the de-duplication reads one id in two overlapping files as the same row exported twice and keeps one. A row one download restated (a corrected price or amount) lost the other version by file name, with no message. Two rows of one id inside one file were already refused; two files are now refused the same way.
- **Fix:** keep the newer download only (delete the other, or the dates it shares with the newer one), or correct the row, and `tjs run`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_brokerage.py` — `_restated_id`, `_RESTATED_FIELDS`

### "Error: kr_ledgers.csv: Kraken ledger kr_ledgers.csv: refid RZ*** (2025-03-02)" — the leg "is under the books' zero (1e-09 units) but not negligible"
- **Check:** the paragraph names the coin, its tiny amount and its USD value (more than 0.01 USD); the crypto account's files are not booked (exit 1). A dust leg that is worth nothing gives only a note ("dust sweep" / "instant trade … not booked").
- **Cause:** Kraken writes amounts to ten decimals, so a sweep or trade can spend less than 1e-09 of a coin. Such a leg is left out only when it and its share of the other side are each worth at most 0.01 USD (tax-logic CA-CRYPTO-11); one that carries value would be a real sale or purchase lost. Before v0.21.0 any such leg became a 0-unit trade and stopped the account.
- **Fix:** correct the rows in the export, or enter the trade as `.tt` lines and remove its rows from the export.
- **Fixed in:** `v0.21.0`
- **Code:** `src/taxjson/lib/brokerages/kraken.py` — `_check_dust`, `_drop_dust`, `under the books' zero`

### `tjs audit --account <crypto account>`: "taxjson-audit: CHECKS FAILED — see FAILED lines above. The usual cause is stale work/ artifacts", with "pipeline tie-out 0 tied, 1 MISMATCHED" although `tjs audit` without `--account` ties
- **Check:** a Canadian project with two or more `crypto = true` taxable accounts holding the same coin; the filtered audit's gain differs from `tjs sum` and from the unfiltered audit. Re-running `tjs run` does not change it.
- **Cause:** in Canada the crypto accounts pool each coin's cost (tax-logic CA-SL-13), so the run computes them together. `--account` used to drop the other crypto accounts from the audit's recomputation, so it re-ran the one account's own cost and reported a mismatch that is not in the books.
- **Fix:** upgrade: `--account` now only narrows what is shown; the computation is the run's (every crypto account in one pass). On an older release, run `tjs audit` without `--account` (with `--id` or a symbol to narrow it).
- **Fixed in:** `v0.29.1`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_audit`, `crypto_blend`

### `tjs fx-cash --ledger v2` (or crypto-sends' stablecoin currency gain) shows a large gain on US dollars bought on Coinbase: a CAD-priced USDC Buy with a blank Total entered at zero cost
- **Check:** the Coinbase export has a Buy or Sell of USDC (or another USD stablecoin) priced in CAD whose Total cell is blank; `tjs fx-cash --ledger v2 --json` lists an `FXCONV` from CAD 0.0 for it.
- **Cause:** the v2 cash reader and the crypto-sends pool read a blank Total as 0; the books' parser derives it, but a stablecoin buy is not an event in the books, so nothing refused the row (2026-10 audit #74).
- **Fix:** upgrade: both derive a blank Total as the parser does (Subtotal ± fee, else quantity × price ± fee) and refuse a row with nothing to derive it from, naming it ("the Total cell is blank and neither Subtotal nor quantity x price is there to derive it from"). On an older release, fill in the Total from the Coinbase statement.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/brokerages/coinbase.py` — `coinbase_row_total`, `coinbase_cash_events`, `_derive_blank_total`; `src/taxjson/lib/crypto_sends.py` — `coinbase_pool_flows`

### `tjs fx-cash --ledger v2`: the US-dollar pool on Coinbase keeps the fee of a USDC bought or sold for US dollars
- **Check:** a Coinbase Buy or Sell of USDC priced in USD with a fee; the v2 ledger's USD units stay above the balance by the fees (a fee over the 1.00 tolerance shows as a reconcile gap).
- **Cause:** USDC for USD is no conversion, and the whole row was skipped, fee included; the fee is US dollars spent (CA-FX-07) (2026-10 audit #73).
- **Fix:** upgrade: the fee is a cash flow of the pool. On an older release, add a `CASHMOVE` line for the fee.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/brokerages/coinbase.py` — `coinbase_cash_events`

### `tjs crypto-sends` shows the wrong currency gain for a stablecoin send, and too many pool units (`units_now` in `--json`), with overlapping Kraken ledgers or Coinbase exports in inputs/
- **Check:** two exports of one account cover the same dates (a copy, or an all-history file beside the yearly ones), and `tjs crypto-sends --json` shows `pool.units_now` above what the account holds.
- **Cause:** the stablecoin pool added up each export's flows, de-duplicated only within one file, so shared entries counted twice and the average cost was diluted (2026-10 audit #79).
- **Fix:** upgrade: each flow carries its identity (Kraken refid, Coinbase ID or row) and overlapping exports are read once, keeping the first copy; distinct entries of the same amount still both count. On an older release, keep one copy of the overlapping export while reading the report.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/crypto_sends.py` — `merge_pool_flows`, `build_report`, `kraken_pool_flows`, `coinbase_pool_flows`

### `tjs crypto-sends`: a gift or payment's fair value (and the crypto_sends.tt proceeds) is a few cents off the price x the units
- **Check:** the shown fair value per coin x the units, against the full-precision price the source names; the difference is at most about 5 parts per million of the value.
- **Cause:** the value was computed from the price rounded to six significant digits (the displayed price).
- **Fix:** upgrade; the value and a newly written line use the full-precision price. A line already in crypto_sends.tt is kept as written.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/crypto_sends.py` — `fair_value`, `tt_line`, `keep_written_lines`

### `tjs fx-cash --ledger v2`: NOT COMPUTED for a Kraken account, with "reconcile" gaps (or "no statement balance" / "no opening pool") although the ledger export is complete
- **Check:** look at the Kraken ledger CSV's header and order. It may be written in capitals (`TXID,REFID,TIME,…`), list one day's rows newest first, or have a `feecurrency` that differs from the row's asset. `tjs fx-cash --ledger v2 --json --cash-events` shows no Kraken events, a CASHBAL that is the day's first balance rather than its last, or a fee FLOW in the asset's currency.
- **Cause:** the v2 cash reader read the export differently from the parser: it read the raw header (an uppercased one gave no events, and a missing column gave none instead of an error); it ordered a day's balance rows by file position, not by their time; and it charged every fee to the row's asset, ignoring `feecurrency`.
- **Fix:** upgrade. The reader now reads rows as the parser does (any header case, a missing column refused naming the file), takes each day's balance after its latest row by clock time, and books a fee in its own currency (none for a coin fee, which is in the books). On an older release, re-export the ledger from Kraken (lowercase header, oldest first).
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/brokerages/kraken.py` — `kraken_cash_events`, `_dict_rows`, `_fee_ccy`

### `tjs fx-cash --ledger v2`: NOT COMPUTED for a Kraken account after a reward paid to a wallet asset (`USDC.F`, `USD.HOLD`): the statement balance in the RECONCILIATION table is about the reward alone, a "reconcile" gap of nearly the whole spot balance
- **Check:** the Kraken ledger CSV has `earn` / `reward` rows of an asset code with a wallet suffix (`.F`, `.HOLD`, `.S`, `.M`) whose `balance` column is that wallet's own; `tjs fx-cash --ledger v2 --json --cash-events` shows a CASHBAL on that day equal to the other assets' balances plus the reward wallet's, without the spot asset's.
- **Cause:** the cash reader keyed each asset's balance series by the bare coin, so `USDC.F` and `USDC` shared one series and the day's latest row (the reward wallet's) replaced the spot balance (backlog TJ-0019).
- **Fix:** upgrade: each asset code keeps its own balance series, and the currency's statement balance sums them. On an older release, add a `CASHBAL` line with the balance the statement shows.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/brokerages/kraken.py` — `kraken_cash_events`, `_ASSET_SUFFIX_RE`

### `tjs crypto-sends <acct> --set` from two terminals at once (or beside a `tjs run` that is asking about sends): one of the two decisions is missing from sends.json although both said "saved"
- **Check:** right after both commands printed `saved: <id> = ...`, `tjs crypto-sends <acct>` lists one of the two sends undecided again, and `inputs/<acct>/sends.json` holds only the other.
- **Cause:** each command loaded sends.json, changed its own send and wrote the whole file back; the later write dropped the other's change, and both reported success (audit 2026-10-24 A35).
- **Fix:** upgrade: a decision's load, change and save are one locked step (a hidden `.sends.json.lock` beside the manifest; the second command waits its turn). On an older release, record the decisions one after another, or repeat the one that was lost.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/crypto_sends.py` — `record_decision`, `clear_decision`, `_manifest_lock`, `manifest_lock_path`; `src/taxjson/lib/safe_write.py` — `file_lock`

### `tjs crypto-sends`: too few pool units (`units_now` in `--json`) and the wrong currency gain, with two crypto accounts whose Coinbase exports have no ID column and hold identical rows
- **Check:** two `crypto = true` accounts each bought the same quantity of the same stablecoin at the same price and time, in exports of Coinbase's older layout (no `ID` column); `tjs crypto-sends --json` shows `pool.units_now` short by that quantity.
- **Cause:** the pool reads a row once across overlapping exports (a copy, an all-history file beside the yearly ones); that rule was applied to every account's exports together, and a row with no ID is identified by its cells, so the second account's identical purchase counted as a copy of the first's (audit 2026-10-24 A36).
- **Fix:** upgrade: each account's exports are merged by themselves, then the accounts are added up. On an older release the pool is short by those rows; do not rely on its gain until you upgrade.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/crypto_sends.py` — `account_pool_flows`, `merge_pool_flows`, `build_report`

### `tjs crypto-sends` pairs a send with a nearby arrival that lost coins and books a `-fee` line, although the exact quantity arrived in another of your accounts a day later
- **Check:** the listing (or `--json`: `matched`, `network_fees`) shows a network-fee line for a send whose full quantity did arrive on another of your accounts within 3 days, while an arrival of fewer coins closer in time took its place (and its own send, or the later arrival, is left unmatched).
- **Cause:** the pairing scored each candidate with a weighted sum of coins lost and time apart, so an arrival minutes away that lost a little beat an exact arrival a day later — against the stated order: the most sends paired, then the fewest coins lost, then the closest in time (tax-logic CA-CRYPTO-06) (audit 2026-10-24 A46).
- **Fix:** upgrade: the three are compared in that order, exactly. On an older release, check each `-fee` line against the arrivals the listing shows before writing crypto_sends.tt.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/crypto_sends.py` — `match_transfers`, `_assign`

### `tjs fx-cash --ledger v2`: a "reconcile" gap of a few units on a Kraken book after a USDC (or another USD stablecoin) was sold or bought for US dollars off par; or the Coinbase USD pool stays short by the discount on a USDC bought below par
- **Check:** the Kraken ledger has a `trade` refid whose two legs are a stablecoin and USD in different amounts (200 USDC out, 197 USD in), and the gap is their difference; on Coinbase, a USDC Buy priced in USD whose Total is below the quantity. `tjs fx-cash --ledger v2 --json --cash-events` lists no event for the trade.
- **Cause:** a USD stablecoin is US-dollar cash (CA-CRYPTO-02), so the trade is one currency for itself and no FXCONV — but the pool moved by the spread, as the export's own balances show. The Kraken reader dropped the trade whole; the Coinbase reader booked only a spread paid (audit 2026-10-24 C06, C07).
- **Fix:** upgrade: the net movement is a FLOW of US dollars in either direction (an amount in is taken at the day's rate; the de-peg is not a gain or loss of the coin). On an older release, add a `CASHMOVE` line for the difference.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/brokerages/kraken.py` — `kraken_cash_events`; `src/taxjson/lib/brokerages/coinbase.py` — `coinbase_cash_events`

### `tjs fx-cash --ledger v2`: a Kraken currency conversion is missing (a "reconcile" gap of its whole amount) when its refid has two receipt legs; or "Kraken ledger kr_ledgers.csv: refid RZ*** (2026-01-02) trades 3 currencies at once"
- **Check:** `tjs fx-cash --ledger v2 --json --cash-events` shows no FXCONV for the refid, and in the ledger export its rows are one USD debit and two CAD credits (Kraken wrote one side of the trade as two rows).
- **Cause:** the reader booked a cash-only refid only with exactly one debit and one credit leg, and skipped any other shape without a word (audit 2026-10-24 C08).
- **Fix:** upgrade: the legs are added up per currency, so two receipt legs are one conversion; a refid that trades three or more currencies is refused naming it (enter it as `FXCONV` lines and remove its rows from the export). On an older release, add the conversion as an `FXCONV` line.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/brokerages/kraken.py` — `kraken_cash_events`, `currencies at once`

### `tjs fx-cash --ledger v2`: a Coinbase "Advanced Trade Buy" (or "Advanced Trade Sell") of USDC priced in CAD is in neither book — no FXCONV, a "reconcile" gap or a USD balance nothing explains
- **Check:** `tjs fx-cash --ledger v2 --json --cash-events` lists no event for the row, while the same row labelled `Buy` gives an `FXCONV CAD -> USD`; `tjs run` counts the row as a stablecoin conversion non-event (`run --details`).
- **Cause:** the position parser recognises Advanced Trade labels (the word Buy or Sell anywhere in the type) and leaves a stablecoin's conversion to the cash model, but the cash reader accepted only the exact labels Buy and Sell (audit 2026-10-24 C09).
- **Fix:** upgrade: the cash reader reads the row's kind as the parser does. On an older release, add the conversion as an `FXCONV` line.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/brokerages/coinbase.py` — `coinbase_cash_events`, `_BUY_RE`, `_SELL_RE`

### `tjs fx-cash --ledger v2`: NOT COMPUTED for a Coinbase account, "the statement balance at the end of … is 80.00, the ledger's 75.00 … — a conversion, deposit or withdrawal the ledger does not see", after a USDC "Rewards Income" row priced in CAD
- **Check:** `tjs divs` lists the reward as staking income in CAD (`DIVIDEND … USDC 5 CAD 1.37 6.85`); the gap is the reward's USDC amount; the same row priced in USD reconciles.
- **Cause:** a USD stablecoin is US-dollar cash (CA-CRYPTO-02), but the reward was booked only as income in its row's currency (CAD), so the ledger never took the US dollars in (backlog TJ-0038).
- **Fix:** upgrade: a reward paid in a USD stablecoin (or a fiat currency) and priced in another currency brings its own units in, at the income's value when that is in the base currency (else at the day's rate; `tax-logic` CA-FX-07). On an older release, declare it: `CASHMOVE <date> USD 5.00 cost=6.85 at=coinbase`; after upgrading, remove that line.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/fx_cash_v2.py` — `_reward_cash`, `build`

## Currency rates

### "Error: no exchange rate for 1 row(s): USD->CAD on 2025-09-25 (BUYSELL QZQ.US; no rates for currency)." (often after "Warning: download failed — Bank of Canada FXUSDCAD …")
- **Check:** before it the run printed "Info: TAXJSON_OFFLINE is set — using cached rates only (no download)", or a "download failed" warning ("Bank of Canada FXUSDCAD", "Bank of Canada noon USDCAD" or "Yahoo Finance USDCAD=X"), and "Info: FX USD→CAD: Bank of Canada rates". `tjs run --details` adds "Dates it would have covered have no rate this run." under the warning and counts the dates ("Info: FX USD→CAD: Bank of Canada Valet for 0 dates"). The run stops at "merging and converting the account's books" (exit 1).
- **Cause:** taxjson converts each row at the rate of its date, or the latest rate of the 5 days before it, and never uses a built-in rate. Offline (`TAXJSON_OFFLINE=1`) with an empty or old rate cache (`~/.currency_price_cache.json`), or with the Bank of Canada Valet API (or the Yahoo fallback before 2017) unreachable (no network, a firewall or proxy, an outage), there is no rate; a failed download is never replaced by another source's rate. The same message for another currency (`EUR->CAD`) means that currency is not in `[settings] source_currencies`, so its rates were never fetched.
- **Fix:** run `tjs run` once online (unset `TAXJSON_OFFLINE`); the rates are cached and later offline runs use them. For a new currency, add it: `source_currencies = ["USD", "EUR"]`. If no source has a rate for that date, enter the row in CAD at that date's rate as a `.tt` line.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_convert_currency.py` — `missing_rate_message`, `no exchange rate for`; `src/taxjson/bin/to_base_curr.py` — `resolve_rows`, `refresh_boc`, `using cached rates only`, `download failed —`; `src/taxjson/bin/taxjson_run.py` — `stage_currency_rates`

### "Error: no exchange rate for N row(s)" for rows dated before the project's year, online, after an export was re-saved as UTF-16
- **Check:** the file starts with a UTF-16 byte-order mark (`file inputs/<account>/*.csv` says "UTF-16"); the missing rates are all dated before January of the project's year; the run was online.
- **Cause:** the rates stage asks only for the dates the project's files reach, read from their bytes; a UTF-16 file's bytes matched no date, so the window started at the project's year and older rows had no rate (2026-10 audit #40). Every parser reads UTF-16, so nothing else said so.
- **Fix:** upgrade: a UTF-16 file is decoded before its dates are read. On an older release, save the export as CSV UTF-8 and run again.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/rates_window.py` — `earliest_in_files`

### "Error: rates file work/to_base.csv, line 1: rate '1e400' for USD->CAD on 2026-01-02 is not a positive finite number — refusing to convert money with it", or "Error: BUYSELL ZZQ.US 2026-01-02 (id=…): net_amount=1e+300 x rate 1E+10 (USD->CAD) is not a finite number — refusing to convert money with that rate"
- **Check:** the rates-file line the first message names, or the row and rate the second quotes: the rate, or an amount times it, is beyond what a number this tool books can hold (`1e400`; `1e-400` reads as 0).
- **Cause:** a rate that is a finite decimal but has no float value (too large or too small) passed the reader's checks and turned every amount it was applied to into `inf` (or 0), relabelled in the target currency, at exit 0 (audit 2026-10-24 B33). Both are refused now: the rates reader refuses the rate as it refuses NaN, and the conversion stops when a result is not a finite number. Only a hand-edited rates file or `--default-rate` holds such a rate; the rates `taxjson run` fetches are ordinary numbers.
- **Fix:** correct the rate (or regenerate the file: delete `work/to_base.csv` and `tjs run`), or the amount when the row is the error.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_convert_currency.py` — `load_exchange_rates`, `convert_transaction`, `is not a finite number`

### `tjs run`: "Error: no exchange rate for 12 row(s): USD->CAD on 2006-01-03 …" for an IB Flex export whose dates are 8 digits (`20060103`) and whose header also writes a date in another shape (`WhenGenerated`, the period)
- **Check:** the rates file's first date (`work/to_base.csv`, and the start stamp beside it) is later than the export's oldest trade date; the export writes its dates as 8-digit runs and one line of it writes a date another way.
- **Cause:** the rates window starts at the earliest date written in the project's files, and an 8-digit run was read as a date only in a file holding no date of another shape (elsewhere it is an id or an amount), so one header line in another shape hid every compact trade date and the window started at January 1 of the project year (2026-10-24 audit A38).
- **Fix:** upgrade: an 8-digit date in a cell of a column whose header names a date (`TradeDate`, `Date/Time`, `SettleDateTarget`) is read in any file, and `tjs run` fetches the missing years. A header row names "date" as a word and holds no number or date, so a data cell that merely contains the letters (a description `CONSOLIDATED ...`, an action `Liquidated`) never hides the dates after it, and a file whose dates are all 8 digits is still read whole (2026-10-25 audit diff-engine#1). On an older release, put a `.tt` line dated at the oldest trade (an `OPENING` of the position it opened) in the account's folder: the window starts there.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/rates_window.py` — `_compact_in_date_columns`, `_date_heads`, `_cells`, `earliest_in_text`, `_DATE_HEAD`

### `tjs run`: "Error: no exchange rate for 12 row(s): USD->CAD on 2019-03-15 …" for an export whose header is written in capitals with its words run together (`TRADEDATE`, `SETTLEDATE`) and whose dates are 8 digits (`20190315`)
- **Check:** the rates file's first date (`work/to_base.csv`) is later than the export's oldest trade; the header cell over the dates is all capitals with no separator; and the file writes a date in another shape somewhere (a note `report 2024-06-01`, a generated stamp).
- **Cause:** an 8-digit date is read in any file when its column's header names "date" as a word (`TradeDate`, `Date/Time`, `TRADE_DATE`). `TRADEDATE` has no word break, so the column was not read, and with a date of another shape in the file the 8-digit runs elsewhere were taken for ids: the window started too late. A file whose dates are all 8 digits was read whole and was not affected.
- **Fix:** upgrade: in a header row written all in capitals (every cell a capitals name, `TRADEDATE,SYMBOL,NOTE`), a cell holding `DATE` names a date column; a data row (one holding an amount, a date or a cell like `ZZQ.US`) never does. On an older release, rename the header cell `TRADE_DATE`, or put a `.tt` line dated at the oldest trade in the account's folder.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/rates_window.py` — `_CAPS_NAME`, `_date_heads`, `_compact_in_date_columns`

## Options

### "Warning: 1 option contract(s) written in 2025: is 400.00 of premium taxed in 2026 right? — `taxjson checklist`"
- **Check:** `tjs run --details` words it "1 option contract you wrote in 2025 and closed in 2026 is on transition close timing: 400.00 of premium is taxed in 2026", with the question under it. `tjs option-boundary` lists the contract with "QUESTION: did your 2025 return report the … premium when the contract was written?"; `tjs checklist` lists option-boundary as a "! 24 option-boundary: …" line (`--all`: `[!]`, or `[>]` when it is the next step) with the same question. The project's `[settings] option_grant_timing_since` is the project year (the default: `tjs init` leaves the key commented) and no `filed/2025.json` lock (or `prior_year_record`) records how 2025 was filed.
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

### `tjs option-boundary`: "error: work/margin_base.json has no "transactions" list — the books are damaged or from an interrupted run"
- **Check:** `work/margin_base.json` is a JSON object without its `transactions` rows. On v0.28.1 and older the command stopped with an `AttributeError` traceback (issue #53).
- **Cause:** an interrupted `tjs run`, a disk that filled up, or a hand-edited file.
- **Fix:** `tjs run`, which rebuilds `work/`.
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_option_boundary`

### `tjs stats`: written-option trades missing after a split of a long option
- **Check:** `tjs stats YEAR --json` shows fewer written-options trades than `tjs option-yield` lists written contracts closed, and the series had a SPLIT while you held it long.
- **Cause:** the split added the old long balance twice, so the later sale closed phantom longs instead of writing.
- **Fix:** upgrade.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/trade_stats.py` — `written_option_trades`

### `tjs stats`: long-shares P/L off after an assigned put's shares split or changed ticker
- **Check:** a put was assigned, the shares later split or were renamed, and `tjs win-rates --by ticker` shows the sale's P/L off by the premium or a share of it.
- **Cause:** the premium credit stayed in pre-split units under the old symbol.
- **Fix:** upgrade.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/trade_stats.py` — `credits_by_sale`

### `tjs stats` / `tjs option-yield`: a write whose commission exceeds the premium shows a profit
- **Check:** the write's row has a negative net amount, and the view shows a positive P/L for its close.
- **Cause:** the helpers took the sale amount as a magnitude.
- **Fix:** upgrade.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/trade_stats.py` — `written_option_trades`; `src/taxjson/lib/option_yield.py` — `contracts`

### `tjs win-rates`: an assigned call's premium lands on an unrelated earlier sale
- **Check:** `tjs win-rates --by month` moves the premium to a sale of the shares 1–3 days before the assignment that is not the assignment's leg.
- **Cause:** the credit went to the first sale in the 3-day window.
- **Fix:** upgrade.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/trade_stats.py` — `credits_by_sale`, `_assignment_legs`

### `tjs option-yield` / `tjs stats`: a renamed option series closes the newer write first
- **Check:** a SPLIT line renames a series into one with an open write, and the buy-back is matched to the later write.
- **Cause:** moved lots were appended behind the destination's.
- **Fix:** upgrade.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/trade_stats.py` — `written_option_trades`; `src/taxjson/lib/option_yield.py` — `contracts`

### `tjs stats`: "exercised long option(s) are not trades" counts another year's exercise
- **Check:** `tjs stats YEAR --details` counts an exercise dated outside YEAR.
- **Cause:** the count was not filtered by the period or book span.
- **Fix:** upgrade.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/trade_stats.py` — `written_option_trades`, `closed_trades`

### `tjs win-rates --by account`: an assigned option's premium comes out of a sale in an account that never wrote the option
- **Check:** two accounts' books carry a sale with the same row id (`tjs events ACCOUNT` for each), and the premium is split between the two sales.
- **Cause:** the credits were keyed by row id alone; an id is unique within one account's book.
- **Fix:** upgrade.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/trade_stats.py` — `closed_trades`

### `tjs stats`: a written option transferred out shows a later purchase of the series as a buy-back, with a profit
- **Check:** `tjs events` shows a TRANSFER row for the series after the write; `tjs stats --json` has a written-options trade dated at a later purchase.
- **Cause:** the replay skipped TRANSFER rows, so the write stayed queued.
- **Fix:** upgrade. A transfer moves the contracts and is no trade; a short that arrives by transfer has an unknown premium, so its close is counted, not a trade.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/trade_stats.py` — `written_option_trades`

### `tjs win-rates`: an assigned call's premium lands wholly on the first of two delivery legs after a split
- **Check:** the underlying split between the assignment and its delivery legs; `tjs win-rates --by month` has the whole premium on the first leg and none on the second.
- **Cause:** the legs drew the credit before the split scaled it.
- **Fix:** upgrade: the replay is in date order, the split first.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/trade_stats.py` — `credits_by_sale`

### `tjs stats`: a renamed option series closes the newer of two same-day writes first
- **Check:** two writes on one day, one in a series later renamed into the other's; the buy-back's trade names the later write's premium.
- **Cause:** the merged queue was sorted by date alone.
- **Fix:** upgrade.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/trade_stats.py` — `written_option_trades`

### `tjs stats`: an assigned put's premium counted twice when the delivered shares covered a short
- **Check:** `tjs stats --json`: the cover's short-shares P/L holds the premium (the engine cut the delivered shares' cost) and the written-options trade has it too.
- **Cause:** the credit went to sales only; a cover is a purchase.
- **Fix:** upgrade: the shares the gains rows book as a short cover carry their part of the premium.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/trade_stats.py` — `credits_by_sale`, `closed_trades`

### `tjs win-rates`: a cash-settled assignment (an index option, or an assignment with no share leg within a week) takes its premium out of a later unrelated sale of the underlying
- **Check:** the option's ASSIGN row carries the cash paid and no share leg is dated within 3 days before to 7 after it; a later sale of the underlying is short by the premium.
- **Cause:** every assignment made a share credit, drawn by any later sale.
- **Fix:** upgrade. The engine's own test decides (CA-OPT-08): no stock line of the underlying in the account, or no leg in its window, and the premium stays with the option trade, where the engine leaves it (if the leg is missing from your input, add it and re-run).
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/trade_stats.py` — `written_option_trades`, `credits_by_sale`, `_assignment_legs`

### `tjs win-rates`: an assigned put's premium comes out of a sale made before the shares were delivered
- **Check:** a sale of the underlying between the option row and the delivery leg loses the premium; the sale after the delivery keeps it.
- **Cause:** the credit opened at the option row, not at the delivery.
- **Fix:** upgrade.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/trade_stats.py` — `credits_by_sale`, `_assignment_legs`

### `tjs stats` / `tjs win-rates`: after a denied loss ("Denied by the superficial-loss rule (NOT subtracted above)", US: "the wash-sale rule"), the replacement's sale shows a loss or a smaller win, and NET_P/L counts the denied loss twice
- **Check:** `tjs wash-sales` lists the loss and its replacement; the replacement was sold since. In `work/<account>_gains_wash.json` (else `_gains.json`) that sale's `cost` holds the denied amount the rule added, and the round trip's NET_P/L is lower than what you sold for minus what you paid by that amount.
- **Cause:** the views counted each sale's `raw_gain`. That undoes a loss sale's own denial, but a replacement's cost still held the denied loss the rule added to its ACB (s.53(1)(f); US: its basis, §1091(d)), so the loss counted once in the loss sale and again in the replacement's sale.
- **Fix:** upgrade, then `tjs run`: each sale's row in the gains files says how much of an earlier denied loss its cost carries (`deferred_loss_recovered`; a US replacement sold before the loss: `wash_basis_added`), and the views add it back. A denied loss counts once, in the loss sale's P/L. The filing figures do not change.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/trade_stats.py` — `economic_pnl`, `closed_trades`; `src/taxjson/lib/core.py` — `deferred_loss_recovered`, `make_gain_entry`, `_split_gain_entry`

### `tjs stats` / `tjs win-rates`: a loss denied on an option whose replacement option was then exercised still counts twice (NET_P/L lower by the denied loss)
- **Check:** `tjs wash-sales` lists the option's loss with a replacement contract of the same series, and `tjs events all ACCOUNT | grep SERIES` shows that contract's `ASSIGN` (exercised) into the shares, which were sold since. What you paid and got back nets to more than the view's NET_P/L by the denied amount.
- **Cause:** the denied loss is added to the replacement option's cost (s.53(1)(f); US: §1091(d)), and on exercise that cost rolls into the shares'. The shares' sale carries it, but the tally of earlier denied losses (`deferred_loss_recovered`) stayed with the option, so the view counted the loss in the option's sale and again in the shares' (backlog TJ-0061).
- **Fix:** upgrade, then `tjs run`: the exercise carries the option's tally with its premium into the shares (Canada: the pool; US: the lots), so the loss counts once. The filing figures do not change.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/core.py` — `_AssignPremiumLedger`, `last_deferred`, `internal_dw`, `option_dw`

### `tjs win-rates SYMBOL`: "Denied by the superficial-loss rule (NOT subtracted above): … over N disposition(s)" (US: the wash-sale rule) counts other tickers' denials, and "! N disposition(s) with an unknown cost are not counted" other tickers' sales
- **Check:** `tjs win-rates` with no symbol prints the same denied line; `tjs wash-sales` shows the denials are on other tickers.
- **Cause:** the table was narrowed to the symbols named, but the denied total, its count and the unknown-cost count came from every ticker in the window (backlog TJ-0025).
- **Fix:** upgrade: with symbols given, the denied line, `denied_total` / `denied_count` and `tainted_skipped` in `--json`, and the unknown-cost line cover only the matching tickers (an option under its underlying, as the rows).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/trade_stats.py` — `closed_trades`, `symbol_ok`; `src/taxjson/bin/taxjson_run.py` — `cmd_win_rates`, `_sym_ok`

### `tjs option-yield`: wrong P/L or negative open contracts after a same-day option split
- **Check:** the series has a SPLIT dated the open's own day with a later time.
- **Cause:** the split was replayed, but the lot's unit factor skipped splits dated on the open day.
- **Fix:** upgrade.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/option_yield.py` — `factor`, `close_units_on`, `splits_seen`

### `tjs option-yield`: an option series written in one currency and bought back in another shows a P/L that subtracts one currency from the other (or "! 1 close(s) not in the open's currency: check that row's currency (…)")
- **Check:** `tjs events` for the series: the open and the close rows carry different currencies (e.g. a hand-written .tt line with USD on a .TO series).
- **Cause:** contracts were matched per account and series only; the close's money was taken as the open's currency.
- **Fix:** correct the row's currency (the listing's currency). The report now leaves the contract's premium, P/L and that close's cash unknown (`?`) and names the row; nothing is converted.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/option_yield.py` — `contracts`, `Close`, `_fold`, `render`

### `tjs option-yield`: after an option strike adjustment (an RBC special-dividend adjustment, or a .tt `SPLIT` ratio 1 into another strike's series), the open row shows the new series with the old strike and the notional yield uses the old strike
- **Check:** `tjs option-yield --json`: the open row's `symbol` names the new strike, but its `strike` is the old one.
- **Cause:** `Lot.strike_on` divided the opened strike by the split ratio and never read the renamed series' own strike.
- **Fix:** upgrade; the strike (and the strike notional) are read from the renamed series.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/option_yield.py` — `strike_on`, `notional_on`, `_add_notional_days`

### `tjs t1135`: the cost amount (year-end or maximum) of shares an assigned put delivered, where the delivery also covered a short position, is above `tjs run`'s ACB for them by a share of the put's premium
- **Check:** `tjs t1135 --json` `per_symbol` cost for the shares against the inventory's `total_cost` in `tjs run --details` (or `work/<account>_gains.json`); the books hold a short position in the shares, a written put on them, its assignment, and a delivery of more shares than the short (50 short, 100 delivered).
- **Cause:** s.49(3.1) deducts the put's premium from the cost of the shares acquired. The gains engine apportions it by quantity between the shares that cover the short (their share goes into the cover's gain) and the shares kept (their share lowers their ACB). The T1135 walk folded the whole premium into the position before the delivery: covering the short drained it, and the kept shares opened at their gross purchase price, so the walk reported more cost than the engine (a filing figure: the T1135 maximum and year-end cost amount).
- **Fix:** upgrade; the walk stages the premium for the delivery leg and apportions it as the engine does (tax-logic CA-RPT-12). Then `tjs t1135`.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_t1135.py` — `walk_costs`, `pending_fold`; `src/taxjson/lib/core.py` — `_AssignPremiumLedger`, `leftover_adj`

### `tjs option-yield`: after a strike adjustment part-way through the window, the `*` notional yield and `avg_notional` use the new strike for the whole window
- **Check:** `tjs option-yield --json`: a series renamed into another strike's series (a `SPLIT` ratio 1 with `symbol_new`, an RBC adjustment) during the window, and `avg_notional` equals the new strike's notional for every day the contract was open, the days before the adjustment included.
- **Cause:** one per-contract notional (the series' at the window's end) priced the whole stretch.
- **Fix:** upgrade; the exposure is added up stretch by stretch between the lot's splits and renames, each at its own strike.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/option_yield.py` — `_add_notional_days`

### `tjs option-yield` shows a contract transferred out of the account as still open (or "past expiry with no expiry or assignment row"); `tjs checklist` / `tjs option-boundary`: "option position(s) still open past expiry" / "expired … but no expiry/assignment row" for it
- **Check:** `tjs events` shows a `TRANSFER` row on the option series (the contracts moved to another account or broker) after the write, and the views still count the contracts as open.
- **Cause:** the option views skipped `TRANSFER` rows.
- **Fix:** upgrade. A transfer out closes the oldest contracts of the series ("transferred": no money, no P/L, and no yield on them); one in opens contracts whose premium is unknown here (it is in the books the contract came from) and `tjs option-yield` says so (`! N contract(s) received by transfer`); `tjs option-boundary` lists a transferred-out write with where its close is and a transferred-in one with the premium `?`. A move out and back within days that nets to zero (a cancel and rebook) is a no-op, as the gains run reads it.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/option_yield.py` — `_kept_transfer_ids`, `TRANSFERRED`, `transfer_in`; `src/taxjson/lib/option_boundary.py` — `kept_transfers`, `ORIGIN_TRANSFER`, `expired_open`

### `tjs option-yield`: a `.tt` `OPENING … cost=unknown` contract and the trade that closed it both show as open (two contracts, nothing closed)
- **Check:** the closing trade is stamped midnight (Questrade, or a hand-written row with no time) on the day the OPENING line is dated.
- **Cause:** the opening balance and the trade sorted as equals at `00:00:00`, so the close was replayed first and read as a new write (or purchase).
- **Fix:** upgrade; a day's opening balance comes first whatever its stamp.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/option_yield.py` — `contracts`

### `tjs option-yield`: two writes of the same day, one series renamed into the other, and the buy-back closes the later write
- **Check:** `tjs option-yield --json`: the closed row's `opened` is the day's later write (by clock time) although an earlier one of the same day was renamed into the series.
- **Cause:** the merged queue was ordered by open date alone.
- **Fix:** upgrade; the queue keeps the openings' replay order.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/option_yield.py` — `seq`

### `tjs option-boundary`: after an option split the premium of a later buy-back is multiplied (2-for-1: twice the premium); a renamed series is listed under its old name and flagged "expired?" by the old expiry; a renamed older write closes after the destination's newer write
- **Check:** the series has a `SPLIT` row (a ratio, or `symbol_new`) between the write and the close. Twice the premium: `tjs option-yield` shows the right premium for the same close. The old name: the row's `SYMBOL` is the series before the rename, and "expired" names the old series' expiry date.
- **Cause:** the lot kept its as-written units for the premium per contract, its old symbol, and a moved lot was appended behind the destination's.
- **Fix:** upgrade; the premium is per contract in the close's denomination, the lot is the renamed series, and the queues merge in write order.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/option_boundary.py` — `write_lots`, `factor`, `seq`

### `tjs option-boundary`: a written contract held before the data (a short `OPENING` line, `--include-options`) never appears, and the buy-back that closed it is matched to a later write instead
- **Check:** `tjs events` shows a short opening balance of the series, a later write and one buy-back; `tjs option-boundary` places the later write's premium at the buy-back and still asks for the first contract's expiry row, or lists the later write as open after the buy-back.
- **Cause:** a short opening balance changed the position but queued no lot, so the first buy-back took the later write.
- **Fix:** upgrade. The before-data contract is a lot of its own (premium `?`), first in the queue: its close is listed with ATTENTION — add the export covering the write (`tjs find-missing-history`); until then its premium's amount and year are unknown — and the later write stays open with its premium.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/option_boundary.py` — `ORIGIN_BEFORE_DATA`, `_unknown_rows`; `src/taxjson/bin/taxjson_run.py` — `PREMIUM ?: the write is not in the books`

### `tjs handoff`: no "written option(s) on another premium timing" line for a contract carried out of the closed year, when another taxable account bought the same series
- **Check:** the write is in one taxable account and a purchase of the same series, within the closed year, in another; `tjs option-boundary` (per account) lists the write as open or closed later.
- **Cause:** handoff hands every taxable account's book to the option walk at once, and its positions were keyed by symbol alone, so the other account's purchase closed the write.
- **Fix:** upgrade; the walk is per account and symbol.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/option_boundary.py` — `write_lots`; `src/taxjson/lib/handoff.py` — `_timing_issues`

### `tjs run`: no "ATTENTION: … the broker marks the sale … CLOSING … but the books hold no long position" for an option close stamped before the day's opening trade
- **Check:** the export stamps clock times (IB); a sale coded `C` at 09:00 has nothing to close, and a purchase coded `O` of the same contract at 15:00 the same day follows it.
- **Cause:** the same-day workaround for exports with no clock time (RBC) excused the close by any later opening of the day, timestamps or not.
- **Fix:** upgrade; an opening stamped later than a clocked close no longer backs it (rows without a clock, or at one stamp, still do). The missing purchase is the fix (`tjs find-missing-history`), or the ticker.map line the warning names.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/option_close_check.py` — `_clocked`, `unbacked_option_closes`

### `tjs option-boundary` / `tjs checklist`: "=> T1-ADJ 2024: remove the … premium reported for 2024 (s.49(4))" although 2024 was amended and re-closed
- **Check:** the assignment's file went into the 2024 project's inputs, `tjs run` there showed the drift, and `tjs close-year --force` re-closed 2024; `filed/2024.json` no longer reports the premium (its line 15300 form line, or its `filed_dispositions`), yet the next year's `tjs option-boundary` still asks for the T1-ADJ and its `tjs close-year` stops on option-boundary.
- **Cause:** an assignment after the grant year was filed asked for a T1-ADJ whenever that year had a lock: nothing read whether the lock still reported the premium.
- **Fix:** upgrade: once the year's lock no longer reports the premium (the amended return), the row says "no amendment — filed/2024.json no longer reports the … premium for 2024 (the amended return, s.49(4))" and the checklist step clears. A lock that still reports it (not re-closed since the amendment) still asks for the T1-ADJ: amend, then `tjs close-year --force` in that year (docs/filing.md step 6).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/option_boundary.py` — `reported_premiums`, `straddling`, `filed_locks`, `written_as`, `the amended return`; `src/taxjson/bin/taxjson_run.py` — `cmd_option_boundary`

## Planning views (rebuy, div-estimate)

### `tjs winners` / `tjs losers`: PROCEEDS − COST is not the GAIN on a ticker with a denied loss (`ZZA.TO  1  300.00  900.00  -360.00`)
- **Check:** `tjs wash-sales` lists a denial on the ticker; the difference is the denied amount.
- **Cause:** the COST column is the cost the engine booked and the GAIN the allowed gain, after the superficial-loss (US: wash-sale) denial; the table had no column for the denial (backlog TJ-0120).
- **Fix:** upgrade: when a row shown has a denial, a DENIED column sits before GAIN and the legend says GAIN = PROCEEDS − COST + DENIED; `--json` rows carry `denied`. Schedule 3 shows it the other way, as a reduced ACB (`tjs form-export`).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_ranked_view`, `with_denied`

### `tjs winners ACCOUNT` / `tjs losers ACCOUNT`: "TOTAL REALIZED GAIN: … (N ticker(s), all accounts; …)" under one account's total
- **Check:** the total is the account's own (`tjs winners` with no account gives another one).
- **Cause:** the label was a fixed string (backlog TJ-0183).
- **Fix:** upgrade: the line names the account (`(3 ticker(s), margin; the losers: tjs losers)`).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_ranked_view`, `all accounts`

### `tjs wash-radar margin` says EXITABLE ("Selling the FULL position at a loss is fine now") or COOLING for a listing that `tjs wash-radar` (no account) calls VIOLATION, WASHED or EXITABLE with a larger TAXABLE count
- **Check:** run `tjs wash-radar` with no account and compare the row; `tjs wash-sales` after the sale shows what the engine did. On a fixed release the scoped view prints "ROWS: the listings of margin; every account's books decide each status".
- **Cause:** with an account, the radar read that account's books alone (`reports/wash_radar_<account>.rpt` too), so another taxable account's rebuy, holding or loss was invisible. The engine pools your taxable accounts (Canada, CA-SL-03; US, US-WASH-04): selling "the full position" in one account while another still holds the rebuy keeps the loss superficial, and a US loss a sibling's purchase already washed read COOLING. Now the account only scopes the rows shown; every taxable book decides each status, and TAXABLE is all your taxable accounts.
- **Fix:** upgrade, then `tjs run` (it rewrites the per-account reports). On an older release, use `tjs wash-radar` without an account.
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_wash_radar`, `stage_cross_reports`; `src/taxjson/bin/taxjson_wash_radar.py` — `--show`, `_shown_for_line`

### US `tjs wash-radar`: "WASHED: QZG.US: the loss is disallowed; no sale undoes it" for a sale a `.tt` `ALLOWLOSS` line claims
- **Check:** `tjs wash-radar --details` says "… is claimed as your filing position against §1091 (ALLOWLOSS inputs/margin/al.tt:1): the rule would disallow …; the books allow it", and `--json` has `"disallowed": 0.0` for the sale.
- **Cause:** the default view's one-line action for a WASHED row was the same whatever the filing position, so a loss the books allow (US-WASH-25) read as disallowed. A row whose every washed loss is claimed now reads "the loss is claimed as your filing position (ALLOWLOSS): allowed, no basis added".
- **Fix:** none needed: the books follow the `ALLOWLOSS` line (nothing is added to the replacement's basis). Delete the line to apply the rule.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_wash_radar.py` — `_render_brief`, `_CLAIMED_ACTION`, `claimed_only`

### Canada: right after a buy, `tjs wash-radar` says RISK (or BLOCKED) and `tjs sell-check` "SAFE*", and the verdict changes once the buy settles
- **Check:** the buy is in the TAXABLE or SHELTERED column already (it is in the books from its trade date), and `tjs wash-radar --date` with the buy's settlement date shows LOCKED (a TFSA or RRSP buy) or VIOLATION (a taxable rebuy after a loss sale).
- **Cause:** the radar read each holder's balance on the settlement timeline as of today, so a buy made today (Friday, settling Monday) was in the window but held nothing until it settled. A loss sale then was denied by the engine (for good, for the TFSA's part) while the radar and `sell-check` called it safe, and `rebuy` did not list the rebought loss. The balance is now read as the books stand at the window's end, trades made today included.
- **Fix:** upgrade. On an older release, run `tjs wash-radar --date` (and read `sell-check`) as of the buy's settlement date.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_wash_radar.py` — `_capacity`, `_bal_at`

### `tjs harvest`: "warning: price cache entries not used — fetched for another quote spelling, or before the spelling was recorded: QZQ (cached for QZQ111-USD, wanted QZQ222-USD). Refresh online."
- **Check:** ticker.map has a `QUOTE` or `CRYPTO` line for the symbol that differs from the spelling the cache names, or the cache (`work/.price_cache.json`) was written by an earlier release (its entries have no `quote_symbol`). With TAXJSON_OFFLINE the run stops instead: "current prices for QZQ are not in the price cache".
- **Cause:** a cached price is the price of the Yahoo spelling it was fetched for. After a `QUOTE` / `CRYPTO` line changes the spelling, the old entry is another asset's price; an earlier release served it as the symbol's price. Entries now record their spelling, and one for another spelling, or with none, is not used.
- **Fix:** run `tjs harvest` once online (without TAXJSON_OFFLINE) to fetch the new spelling's price; the cache is rewritten with it.
- **Fixed in:** `v0.29.1`
- **Code:** `src/taxjson/lib/price_chain.py` — `fetch_prices`, `price cache entries not used`

### `tjs rebuy`: REBUY ON falls on a Saturday or Sunday, or is a day later than the first trade that settles after the window (31 days after the sale's settlement date)
- **Check:** `tjs rebuy --details` lists each sale with its trade date; `tjs wash-radar --all` shows the same date as CLEARS and `tjs buy-check SYMBOL` as its safe-from date. On a fixed release REBUY ON is always a trading day.
- **Cause:** in Canada the superficial-loss window runs 30 days after the loss sale's settlement date (s.54, CA-SL-01), and a buy counts on its own settlement date. The radar gave the day after the window closed, read as a trade date (settlement + 31): a weekend day when the window closed on a Friday or Saturday, and a day late otherwise, since a trade on the window's last day already settles after it. The re-entry date is now the first trading day, on the listing's calendar, whose buy settles after the window (CA-PLAN-05): the window's last day when a trade then settles the next day, the Friday before when the window closes on a weekend; from that day the loss no longer shows BLOCKED or COOLING. In a US project it is the first trading day from the trade date + 31 (US-PLAN-06).
- **Fix:** upgrade. On an older release the date shown was safe but late: buy on or after it, in any account (an RRSP or TFSA buy before it denies the loss for good).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_wash_radar.py` — `reentry_date`, `_first_trading_day`, `_reentry`, `rebuy_at`; `src/taxjson/lib/rebuy.py` — `build`

### `tjs buy-check`: a VIOLATION says "Wait until 31 days after the LATEST in-window loss sale (see `taxjson wash-radar`)", and wash-radar's CLEARS for it is earlier than `tjs rebuy`'s REBUY ON
- **Check:** `tjs rebuy SYMBOL` shows the REBUY ON date; `tjs wash-radar --json` has the row's `rebuy_at` beside its `clears_at`.
- **Cause:** a VIOLATION's CLEARS date is its sell-by date (the last trade date to rescue the loss), inside the window: a buy that day still adds to the denial. buy-check rightly did not print it as a safe-from date, but sent the reader to wash-radar, whose legend called it "the day its window closes". buy-check now prints the radar's re-entry date (`rebuy_at`, the latest across the class), and the radar's legend says a VIOLATION's CLEARS is the last day to sell.
- **Fix:** upgrade; on an older release use `tjs rebuy SYMBOL` for the date a buy is safe.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_buy_check`, `_class_rebuy`; `src/taxjson/bin/taxjson_wash_radar.py` — `_render_brief`, `last day to sell`

### `tjs rebuy`: "already bought back; sell it by ... to keep the loss" while `tjs wash-radar --details` says part of that loss can no longer be rescued
- **Check:** `tjs wash-radar --details` names each loss of the ticker: "the last trade date to rescue it (...) has passed ... No sale can undo it now" for one, a sell-by date for another. `tjs rebuy --details` lists the same sales; `tjs rebuy --json` gives each loss its `rescue_by` and `rescue_passed`.
- **Cause:** rebuy judged the ticker's row as one: the radar's earliest open sell-by date and "denied as things stand" for every sale, though an older loss's own rescue date had passed (its denial is final). Each loss now carries its own rescue date: the `! ` line names the loss a sale keeps, the older one is counted in "already denied", and `--details` says its units are denied for good. After the re-entry fix such a loss usually leaves rebuy the next trading day; it stays when that day is not one (a holiday after the last rescue day).
- **Fix:** upgrade. On an older release go by `tjs wash-radar --details`: a sale by the sell-by date keeps only the losses it names.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/rebuy.py` — `_row`, `_acts`, `_denied_txt`, `rescue_passed`; `src/taxjson/bin/taxjson_wash_radar.py` — `_rescue_deadline`, `rescue_passed`

### `tjs rebuy SYMBOL` says "No ticker sold at a loss has an open repurchase window." while plain `tjs rebuy` lists some
- **Check:** `tjs rebuy` with no SYMBOL lists every open window; `tjs wash-radar --all` lists every ticker in the books.
- **Cause:** with SYMBOLs rebuy shows only their rows, and when none matched it printed the all-clear sentence meant for the whole project, so a mistyped symbol, or a command name given as one (`tjs rebuy wash-radar`), read as nothing to wait for. Now it says "No loss of SYMBOL has an open repurchase window." with an `! ` line per SYMBOL: "not in the books" or "no loss with an open window", and how many other tickers have an open window.
- **Fix:** upgrade; check the spelling (the listing as the books carry it, e.g. `XYZ.TO`, or the bare ticker).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_rebuy`, `others_open`; `src/taxjson/lib/rebuy.py` — `_acts`, `not in the books`, `No loss of`

### `tjs buy-check` / `tjs sell-check` call a loss on covering a short "a loss sold", and US `tjs rebuy` says "only a new short before 31 days replaces it"
- **Check:** `tjs wash-radar --json`: the row's `losses` give each loss's `direction` (SHORT: covering a short); in a US project its `short_cover_loss` has `reshort_ok_from`.
- **Cause:** the Canadian buy-check and sell-check lines and the "last loss sale this tax year" line were written for a long position's sale, and US rebuy gave the short-cover rule as a count of days. Now a ticker whose losses in the window were all on covering a short reads "a loss on covering a short (DATE) within the past 30 days" (buy-check), "a recent loss on covering a short (DATE)" (sell-check) and "last loss this tax year (covering a short)"; US rebuy says "only a new short sale before DATE replaces it", the date buy-check gives (§1091(e)). Verdicts and dates are unchanged.
- **Fix:** upgrade; nothing in the books changes.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_short_cover_loss_date`, `_loss_within_30`, `_last_loss_line`, `_last_loss_by_class`; `src/taxjson/lib/rebuy.py` — `_acts`, `reshort_ok_from`

### `tjs rebuy`: a ticker I sold at a loss is not listed
- **Check:** `tjs wash-sales` and `tjs gains SYMBOL`: was the sale a loss in the books, and when? `tjs rebuy --details` lists the windows closed in the last 7 days.
- **Cause:** `rebuy` lists losses whose window is still open: more than 30 days after the sale (Canada: its settlement) it is gone. The loss is the engine's: in Canada one average cost across your taxable accounts (s.47), so a sale below its own account's purchase price can be a gain. A sale in a registered account is no loss. In a US project a crypto account is outside the wash-sale rule (US-WASH-13), and a futures contract or an option on one is outside §1091 (US-WASH-18).
- **Fix:** none, if one of these applies. If the sale is missing from the books, add the export and `tjs run`.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/rebuy.py` — `build`, `CLEARED_DAYS`; `src/taxjson/bin/taxjson_run.py` — `cmd_rebuy`

### `tjs divs` / `tjs events`: some rows have one more amount at the end, and their columns are shifted left
- **Check:** the rows with the extra field are a broker's that books the withholding on the dividend row itself (IB, with a `09:30:00` time): the last two fields are the gross and the net amount. Before the fix, when the first row of the listing had fewer fields than a later one, that later row's account and action were squeezed into one cell and every column after them moved left.
- **Cause:** the aligner reads the first line as a table header and merges a longer row's leading extras into its first cell (an account name with a space); the row views are positional taxtext with no header, where a dividend with withholding simply has one more field.
- **Fix:** the row views align without a header (`align_columns(header=False)`), the columns lined up with a single-space gap: a row with one more field keeps its columns. The extra field is by design: the net amount after withholding, so a pasted line re-imports as gross and net (`taxjson events` in docs/commands.md).
- **Fixed in:** `v0.30.4`
- **Code:** `src/taxjson/lib/report_model.py` — `align_columns`, `_tokenize`, `header`; `src/taxjson/bin/taxjson_run.py` — `_run_tx_view`, `_tx_display_line`, `net9`

### `tjs divs` / `tjs events`: a crypto reward's quantity prints as `1.2390000000000002e-07`, `5e-08` or `0.0025688399999999998`
- **Check:** `tjs events all crypto | grep SYMBOL`: the rows are Kraken Earn rewards with a same-coin commission (`Staking Reward (net of Kraken commission: gross ... - fee ...)` in the description).
- **Cause:** the net reward was `gross - fee` in binary floating point, which leaves a 17-digit tail on the difference of two 10-decimal ledger amounts; the views then printed the exact value as Python writes it, in scientific notation under 0.0001.
- **Fix:** the parser subtracts on the ledger's decimal digits (`0.0027 - 0.00013116` is `0.00256884`), and the views write every quantity and rate out in full (`0.0000001239`). `tjs run` rebuilds the books; the rows' ids are Kraken's own, so nothing double-books. A derived price (`627.01799999`, the USD value over the coins) is an 8-decimal quotient and stays.
- **Fixed in:** `v0.30.4`
- **Code:** `src/taxjson/lib/brokerages/kraken.py` — `_dec_sub`, `net_qty`; `src/taxjson/bin/taxjson_run.py` — `_tx_display_line`, `sig`

### `tjs div-estimate`: "Warning: no native books for margin (work/<account>_raw.json, _raw_gains.json): not estimated"
- **Check:** the last `tjs run`: did it finish, and did it say "raw holdings skipped for 'margin'"? `ls work/margin_raw.json work/margin_raw_gains.json`.
- **Cause:** the estimate reads each account's native-currency books (the dividend rows `divs-sum` reads, and the native holdings, one row per listing). An account the run did not build, or whose native holdings it skipped because a symbol would pool two currencies, has none.
- **Fix:** run `tjs run`; for a skipped native view, the run's warning names the symbol (a cross-currency rename or a cost adjustment in another currency with no rate on file).
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_div_estimate`, `no native books for`

### `tjs div-estimate`: "Warning: margin: QZD.TO dividend paid on 2024-05-15 while the books show it short"
- **Check:** `tjs find-missing-history margin` and `tjs trades all margin | grep QZD.TO`: does a purchase or a transfer in before the date the warning names (or its ex-dividend date) miss from the books? `tjs div-estimate --details` shows the same line in the payer's "Check:".
- **Cause:** the estimate walks the account's trades by trade date to the dividend's ex-dividend (or record, or pay) date. A short position is charged a dividend, not paid one, so a payment on a date the walk puts the account short usually means the books miss an older trade: an export that starts too late, or shares moved in from another broker. The payment still counts in the estimate (the broker paid it); before, it counted without a word. One warning per account and security (`3 QZD.TO dividends paid from ...` for several).
- **Fix:** add the missing export, or a `.tt` `OPENING … cost=unknown` line for shares held before the data (`tjs find-missing-history --write-missing-history` writes it), then `tjs run`. No trade missing: check the row against the broker's statement.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/div_estimate.py` — `payments`, `short_paid`, `_short_on`; `src/taxjson/bin/taxjson_run.py` — `cmd_div_estimate`, `while the books show it short`

### `tjs div-estimate`: "! 2 payer(s) to check: QZD.TO, QZE.US — tjs div-estimate --details"
- **Check:** `tjs div-estimate --details`: each payer's line ends with what to check — a single payment or an irregular schedule, "1 expected payment not in the books", "2 expected payments not in the books", or "no payment in the last 12 months".
- **Cause:** the estimate follows each payer's own past pay dates. With one payment, or none of the grids (weekly, monthly, quarterly, semi-annual, annual) most of them fall on, it cannot tell the schedule and estimates pro rata from the 12-month amount per share; a payment that the schedule expects more than a few days ago and the books do not have is missing — the exports may end before it, or the payer cut or suspended it (two missing: the projection stops).
- **Fix:** add the newer broker export and `tjs run` if the books end early; otherwise read the line as a planning caveat (the payer's own announcements are authoritative).
- **Fixed in:** —
- **Code:** `src/taxjson/lib/div_estimate.py` — `estimate`, `detect_frequency`, `GRACE_DAYS`

### `tjs div-estimate` calls a regular payer "irregular", or lists a payer you did not expect
- **Check:** `tjs div-estimate --details`: the payer's line lists its payments and typical gap; `tjs divs all | grep SYMBOL` shows the rows behind it. A listing you did not expect (a currency ETF) is there because the books hold a DIVIDEND row for it: a genuine year-end distribution, or a row to report (`tjs events 1y ACCOUNT | grep SYMBOL`).
- **Cause:** the frequency was read from the gaps between consecutive payments, so every extra posting (a special beside the regular payment, a year-end reinvested distribution, a second broker's posting, a reversal re-posted later) broke two gaps, a row without a share count (a Questrade, IB or RBC row whose description names none) was dropped from the schedule, and a single payment was labelled irregular.
- **Fix:** the frequency is the grid most of the last payments fall on (weekly, monthly, quarterly, semi-annual, annual); a special or a stretch not held does not break it, a reversal nets against the payment it cancels, a row without a share count takes the account's nearest, and a single payment reads `1 payment`. Upgrade; a payer still irregular pays on no grid (semi-monthly, every four weeks).
- **Fixed in:** `v0.30.4`
- **Code:** `src/taxjson/lib/div_estimate.py` — `detect_frequency`, `payments`, `FREQUENCIES`, `LOOKBACK`

### `tjs div-estimate`: "! no USD rate in the books: those rows are out of the totals — tjs run"
- **Check:** `ls work/to_base.csv`; `tjs run` downloads the rates its books need (`TAXJSON_OFFLINE=1` reads the cache only).
- **Cause:** the estimate converts at the latest rate the books hold, never online; a payer paying in a currency the books have no rate for has its native amounts but no base-currency figure.
- **Fix:** `tjs run` (online) and run the estimate again.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/div_estimate.py` — `estimate`, `no_fx`; `src/taxjson/lib/price_chain.py` — `latest_rate`

### `tjs div-estimate`: a weekly payer shows as monthly-sized payments, or the per-share amount is double
- **Check:** `tjs div-estimate --details`: the payer's LAST/SH is a multiple of the per-share dividend, and the payer is held in two accounts whose brokers post a few days apart, or in one account with `combined_broker_accounts = true`.
- **Cause:** postings within five days of each other were chained into one payment week after week, and two broker accounts' same-day dividends were added over the larger share count.
- **Fix:** upgrade. Planning numbers only: no filed figure changes.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/div_estimate.py` — `payments`, `schedule`, `source_account`

### `tjs div-estimate`: a traceback ending `ValueError: year ... is out of range`
- **Check:** the command was given a window such as `100000y` or `11y`.
- **Cause:** the 120-month limit applied to `--months` only, not to the positional window.
- **Fix:** upgrade; until then give a window up to `10y` (or `--months 120`).
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_div_estimate`, `longer than 120 months`

### `tjs div-estimate --date 9999-09-01 --months 120`: a traceback ending `ValueError: year 10009 is out of range`
- **Check:** `--date` is within ten years of 9999-12-31 and the window (or the January after the tax year) reaches past it.
- **Cause:** the date and the window were each checked alone; together they left the calendar inside the projection's arithmetic.
- **Fix:** upgrade: the command refuses with `--date 9999-09-01 with a 120-month window reaches past 9999-12-31` (exit 2). Give an earlier date or a shorter window.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/div_estimate.py` — `horizon_problem`; `src/taxjson/bin/taxjson_run.py` — `cmd_div_estimate`, `horizon_problem`

### `tjs div-estimate`: after a stock split (or with `--date` before one), NEXT AMT and the window total are double (or a fraction of) what the payer pays
- **Check:** `tjs div-estimate --details`: the payer's LAST/SH is the per-share amount of the shares before the split, while SHARES is today's count; `tjs events all | grep SYMBOL` shows the SPLIT row after the last dividend, or after the `--date` given (the same books give another forecast with a `--date` after the split).
- **Cause:** each payment's per-share amount was read on the share count of its own day, and the projection multiplied it by today's shares: a 2-for-1 split doubled the forecast without any dividend increase. With `--date` before the split, the split was left out with the rows dated after the date, while the shares stay the books' current, post-split holding.
- **Fix:** upgrade: a payment before a SPLIT row has its share count scaled by the ratio, so every per-share amount is in today's denomination, whatever `--date` (one corporate event, whichever account's book posts it, on one date or two a few days apart). A rename-split (a SPLIT with a new symbol) still leaves the old symbol's history behind: the new listing is estimated from its own payments. Planning numbers only.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/div_estimate.py` — `payments`, `splits`, `split_copies`

### `tjs div-estimate --details`: "payments in CAD too: estimated from the USD ones"
- **Check:** `tjs divs all | grep SYMBOL`: the payer's rows come in two currencies — one broker converts the dividend and another pays it as declared, or one account has a CAD and a USD row of the same pay date.
- **Cause:** the estimate for a position is in the currency that account's latest payment of the payer was paid in, from the payments in that currency; the others are another broker's conversion of the same dividend, not more of it. Before, postings of one date in two currencies were added into one amount labelled with the first currency (USD 10 + CAD 100 read as USD 110).
- **Fix:** none, when the two currencies are two brokers' (each account's estimate is in its broker's currency). Two currencies in one account are worth a look (`tjs events 1y ACCOUNT | grep SYMBOL`).
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/div_estimate.py` — `estimate`, `others`, `schedule`

### `tjs div-estimate`: the withholding total is many times the TAX rows of an account with `combined_broker_accounts = true`
- **Check:** `tjs div-estimate --details`: "withholding 20.0% (the latest payment in margin)" is the rate of the smallest broker account's holding (2 of tax on 10 shares' dividend of 10, beside 90 shares taxed nothing).
- **Cause:** the rate was read from one broker account's posting of the latest payment, the smallest holding's, and applied to the whole position.
- **Fix:** upgrade: the rate is the latest payment's TAX over its gross, every broker account of the account together (2 over 100: 2%).
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/div_estimate.py` — `estimate`, `CLUSTER_DAYS`

### `tjs div-estimate`: a payer vanished, turned irregular, or its history shrank after a dividend was charged on a short position; or `--details` says "was read as a short position's dividend expense, not a correction of an earlier payment"
- **Check:** `tjs divs all | grep SYMBOL`: a negative dividend row dated while the account was short the symbol (a Questrade charge prints a positive count, "ON 100 SHS"; IB a negative one).
- **Cause:** every negative row was netted against the latest earlier positive posting as a reversal, so a short's expense erased the dividend earned while long.
- **Fix:** upgrade. A negative row is read against the position the books hold on the row's record (or pay) date, whatever the row's own share count: the account's trades, scaled by every split and carried through a rename, with the units a `.tt` `OPENING … cost=unknown` line opens. Short, it is an expense, left out of the payer's history; long, a reversal (IB back-computes a negative count on its reversal rows), netted as before. With no trade of the symbol in that account's books, a negative count decides (an expense) and the position's line says so — if that is wrong, the purchase is missing from the books (`tjs find-missing-history`); a positive count is a reversal.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/div_estimate.py` — `payments`, `_short_on`, `_held`, `notes`; `src/taxjson/bin/taxjson_run.py` — `cmd_div_estimate`, `_tt_openings`

### `tjs div-estimate`: LAST/SH is double the dividend for a position partly lent out (a DIVIDEND and a DIVIDEND_IN_LIEU row on the same day)
- **Check:** `tjs divs all | grep SYMBOL`: the pay date has a DIVIDEND row on part of the shares and a payment in lieu on the rest (5 and 5 of a 10-share position).
- **Cause:** the date's amounts were added over the larger of the two counts, the rule for a special beside a regular dividend (two components on the same shares).
- **Fix:** upgrade: the dividend's shares and the payment in lieu's are added — the lent shares are the others (10 on 10: 1/share, not 10 on 5).
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/div_estimate.py` — `payments`, `qty_by`

### `tjs events 24311m`: `ValueError: year 0 is out of range`
- **Check:** the period is a month or year count reaching back before year 1.
- **Cause:** the clamp ignored the current month.
- **Fix:** upgrade, or use `all`.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_tx_period_cutoff`

### `tjs harvest`: "warning: price-chain: ibkr quote for QZQ.US: inf is not a usable price — ignored"; on an older release one holding's market value showed as inf
- **Check:** `tjs harvest --verbose` names the tier. The next run (cache) refuses the entry.
- **Cause:** the IBKR tier accepted any number above 0, infinity included, and no tier boundary checked that a price was finite.
- **Fix:** upgrade. The next tier (Yahoo) prices the symbol, and nothing non-finite is cached.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/price_chain.py` — `_usable_price`, `_ibkr_fetcher`, `_ibkr_option_fetcher`, `fetch_prices`

### `tjs harvest`: "warning: price-chain: yfinance quote for QZQ.TO: currency 'USd' looks like a minor unit (pence/cents) taxjson does not know — not used; the next source is asked" (or "currency ['USD'] is not a code"); on an older release the row was priced in the listing's own currency
- **Check:** `tjs harvest --verbose` names the tier. The holding is priced by the next tier or the cache, or omitted ("no price from any tier for: QZQ.TO"). The same unit in a `work/.price_cache.json` entry (a hand edit) reads "price cache entry for QZQ.TO: … — not used".
- **Cause:** the source named a unit taxjson has no table entry for: the price may be in cents (100 times off) or not a money amount at all. An older release took the quote with no currency, and the listing's currency was then read off its suffix, so a wrongly denominated price could be shown as the market value. Such a hit is now refused like a non-finite price: the next tier is asked, nothing of it is cached, and such a cache entry is a miss.
- **Fix:** none needed when another tier prices it. If every source names that unit, quote another listing of the security with a ticker.map `QUOTE` line, and report the unit (an issue) so the table learns it.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/price_chain.py` — `fetch_prices`, `provider_unit`, `not used; the next source is asked`

### `tjs harvest` with TAXJSON_OFFLINE: "Error: TAXJSON_OFFLINE is set and 1 symbol(s) have no current price in the cache: QZL.L" although `work/.price_cache.json` has an entry for it; online: "warning: price-chain: the quote for QZL.L did not say its unit — that market quotes in pence/cents as well as pounds, so it was not used"
- **Check:** the entry has no `currency` and the symbol is an LSE (`.L`), JSE (`.JO`) or Tel Aviv (`.TA`) line, or a ticker.map `QUOTE` line spells it as one: it was written by a release before `v0.29.1` (which recorded no unit), or by hand. On an older release the entry was served and `tjs harvest` then omitted the row ("pence or pounds — omitted").
- **Cause:** a quote for such a market that does not name its unit could be 100 times off; the live tiers refuse it, but the cache served it. The entry is now a miss like the live quote: online the next tier (Yahoo names the unit) prices it and rewrites the entry; offline a miss stops the run, naming the symbol, as for any symbol the cache lacks.
- **Fix:** run `tjs harvest` once online (unset TAXJSON_OFFLINE): the entry is rewritten with its unit. Or delete the entry.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/price_chain.py` — `fetch_prices`, `minor_unit_listing`, `did not say its unit`

### `tjs harvest` offline after two runs shared `work/.price_cache.json` (two projects with one `--price-cache`, or two runs at once): a symbol one run fetched minutes earlier is "not in the cache"
- **Check:** the first run's `--verbose` output showed the symbol fetched, and the entry is not in the file; the second run was fetching other symbols at the same time.
- **Cause:** each run loaded the cache, fetched its symbols and wrote its whole copy back: the write itself was locked and atomic, but the read-merge-write was not, so the second writer put back the stale copy it had loaded, without the first's new entries. A run now writes only the entries it fetched, merged into the file under its lock.
- **Fix:** upgrade; on an older release, run once more online.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/price_chain.py` — `_save_cache`, `fresh`; `src/taxjson/lib/json_cache.py` — `save_json_cache`, `merge`

### `tjs harvest` offline after a `QUOTE OLD NEW` line: the price shown for OLD is IBKR's quote of OLD's own listing, served as NEW's ("cached for" names NEW)
- **Check:** in work/.price_cache.json, an entry with `"source": "ibkr"` whose `quote_symbol` is the QUOTE target.
- **Cause:** the IBKR tier prices the symbol's own listing (it never sees the QUOTE remap), but the cache recorded the remapped spelling. A later run then served that price as if it were NEW's.
- **Fix:** upgrade and refresh online. An IBKR hit is now cached under its own listing's Yahoo spelling, so the remapped spelling is a cache miss ("price cache entries not used … cached for QZQ, wanted QZOTHER").
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/price_chain.py` — `_ibkr_cache_spelling`, `fetch_prices`

### `tjs harvest`: "Warning: QZL.TO omitted: no GBP->CAD rate — add "GBP" to source_currencies in taxjson.toml, then `taxjson run`"; on an older release "no GBP->CAD rate in …/work/to_base.csv — QZL.TO omitted (run `taxjson run` to refresh rates)", once per account, and the run fixed nothing
- **Check:** the quote is in a currency the books do not hold: a ticker.map `QUOTE` line quotes the holding on another market (`QUOTE QZL.TO QZL.L`), or the price source names that currency. `work/to_base.csv` has no rows for it, and `tjs harvest --json` lists the position under `omitted` with its reason.
- **Cause:** harvest converts each quote to the base currency with the run's rates, and `tjs run` fetches the rates of the `[settings] source_currencies` only, never a currency only a `QUOTE` line's listing is in. The row was left out with one warning per account whose one-line form ended before the symbol, the advice (`taxjson run`) fetched nothing new, and `--json` had no trace of the row.
- **Fix:** add the currency to `source_currencies` in taxjson.toml (`source_currencies = ["USD", "GBP"]`), then `tjs run` and `tjs harvest`. Or quote a listing in a currency the books already hold.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_harvest.py` — `no_rate`, `_omit`, `omitted`; `src/taxjson/bin/taxjson_run.py` — `source_currencies`

### `tjs harvest` or `tjs run` prints lines with no label: "HTTP Error 404: {"quoteSummary":…}", "$QZQ.TO: possibly delisted; no timezone found", "Cookie/crumb fetch failed (DNSError), continuing without crumb", "Failed to get ticker 'QZQ.TO' reason: …"
- **Check:** the lines come just before "Warning: no price from any tier for: QZQ.TO — rows omitted." (harvest) or "Warning: in-kind move(s) between your taxable and registered accounts NOT booked" (run, valuing an in-kind move). The symbol is one Yahoo does not know (a made-up or delisted ticker, or a spelling a ticker.map `QUOTE` line should give), or the network is down.
- **Cause:** they are yfinance's own log lines for a lookup that found nothing. Only `tjs tips --online` silenced them; harvest's price tier and the run's close lookup for an in-kind move let them through.
- **Fix:** upgrade: the price lookups silence yfinance, and the labelled warning after them says what was missed. Then fix that warning's cause: a `QUOTE` line with the Yahoo spelling, an `INKIND` line with the value, or the network.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/price_chain.py` — `_quiet_yfinance`, `_yfinance_fetcher`, `_yf_history_fetcher`

### US `tjs harvest`: LT_IN of a wash-sale replacement counts a whole year from its purchase (326d where a sale is long-term in about 143 days)
- **Check:** `tjs wash-sales` shows the loss the replacement took; `tjs form-export --form 8949` books a sale of such a lot long-term sooner than LT_IN said.
- **Cause:** LT_IN counted from the position's start (the replacement's purchase date). A wash-sale replacement's holding period includes the days the loss shares were held (§1223(3), US-WASH-10), which the engine applies to each lot at the sale.
- **Fix:** upgrade, then `tjs run`: the US books carry each position's `holding_start_date` (the earliest lot's holding period start), and LT_IN counts from it. Books written by an older release fall back to the start date until the run.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/core.py` — `holding_start_date`, `effective_acq_date`; `src/taxjson/bin/taxjson_harvest.py` — `holding_start`, `_days_to_long_term`

### US project: "Error: `taxjson instalments` is Canada-only (CRA instalments (ITA s.156); US estimated tax is not modelled); this project is country = "usa"" after `tjs plot-tax` named it
- **Check:** `tjs plot-tax` on an older release ended "the instalments it calls for: tjs instalments." in a US project, and its year line named "a T5 box 18 or §1256 reclassification".
- **Cause:** the chart's footer named Canada's instalments command whatever the project's country (US-CTRY-02 refuses it in a US project), and the year line named both countries' adjustments.
- **Fix:** upgrade: a US footer names `tjs estimate` only, and the year line the §1256 fold. US estimated tax is not modelled; for the payments, see the IRS Form 1040-ES instructions or a tax professional.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/tax_plot.py` — `render`, `command_country_problem`

### `tjs watch`: "Warning: state file .watch_state.json is damaged ("harvest_now" is not a number) — recording a NEW baseline"; or, on an older release, a `TypeError` traceback from `diff_harvest`
- **Check:** `work/.watch_state.json` (or the `--state` file) has a `harvest_now` that is not a number (text, a list, NaN) and no `radar` field (or `"radar": null`).
- **Cause:** the state's shape check returned as soon as it found no radar, so a damaged `harvest_now` was accepted and the harvest diff crashed on it (audit 2026-10-24 B07). The two fields are checked on their own now, and a damaged state is treated like every other unusable one: a warning and a new baseline.
- **Fix:** nothing to do: the run records a baseline, and the changes since the previous run are not reported that once. If you edited the file by hand, delete it instead.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_watch.py` — `_state_shape_problem`, `load_state`

### `tjs watch`: "ZZQ.US: LOCKED -> CLEAR — held only in sheltered account(s): nothing to sell at a loss (a sheltered disposition has no tax effect)"; or, on an older release, "ZZQ.US: LOCKED -> CLEAR — safe to sell at a loss (don't buy back for 30 days)" for a ticker held only in a TFSA, RRSP or IRA
- **Check:** `tjs wash-radar --all` (or `tjs sell-check ZZQ.US`): the ticker's TAXABLE count is 0 and its sheltered count is not.
- **Cause:** every LOCKED -> CLEAR transition was reported as a loss-sale opportunity, whoever held the units. A position held only in registered accounts or IRAs has no taxable loss to sell — a disposition there has no tax effect — and `tjs sell-check` already said so (audit 2026-10-24 B08). `tjs watch` now reads the radar row's taxable and sheltered quantities and words the transition as sell-check does. A baseline saved before the quantities were recorded has none, and such a row keeps the old line.
- **Fix:** nothing to sell; the line is information. `tjs sell-check ZZQ.US` has the per-account detail.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_watch.py` — `_sheltered_only`, `diff_radar`

## Before you file

### `tjs edge-cases`: "… of denied losses sits in the cost of the 3 units still held at the end of 2025" names fewer units (or none, or more) than you held on Dec 31
- **Check:** `tjs edge-cases --json`: each `year_boundary.deferred_at_year_end` row's `as_of` is `2025-12-31` since the fix. Before it, the units and the deferred amount moved when a later year's sale or purchase was added to the books: a sale in June 2026 of 2 of the 5 units held on Dec 31 2025 showed 3 units and 150.00 of the 250.00 deferred.
- **Cause:** the section read the gains file's final inventory, after every trade in the books, and said those units were held at the end of the project year.
- **Fix:** upgrade and re-run `tjs run`. In Canada the section reads the engine's pools at the end of Dec 31 (`inventory_year_end` in the gains files; a trade-date project is told the day is read on settlement dates): a later trade changes nothing, and a December loss whose replacement is bought in January is in no Dec 31 position (the window across Dec 31 lists it). A gains file written before the fix, and a US project (the US engine has no year-end pools yet), say "held at the end of the books, which may be after 2025" (`as_of` `end_of_data`). The filed figures were never affected.
- **Fixed in:** `v0.36.1`
- **Code:** `src/taxjson/lib/edge_cases.py` — `deferred_into_next_year`, `year_end_pools`, `end_of_data`; `src/taxjson/lib/core.py` — `_pools_copy`, `final_year_end_pools`, `inventory_year_end`; `src/taxjson/bin/taxjson_split_gains.py` — `_split_inventory`; `src/taxjson/lib/pipeline.py` — `engine_options`, `year_end`

### `tjs edge-cases`: a loss window across Dec 31 says "allowed (the replacement was not still held on day 30, or it was sold before the window closed)" while the replacement is still held on day 30
- **Check:** `tjs edge-cases --json`: each `year_boundary.loss_windows` row's `allowed_by` names the reason since the fix (`buyback_exempt`, `filing_position`, `in_kind_contribution`, `not_held_day30`, or `unknown`; `null` when the loss was denied). `tjs wash-sales` lists the loss's denial, if any.
- **Cause:** in Canada every loss the engine denied nothing of got that sentence, unchecked: a written option's buy-back loss (exempt under `option_buyback_loss_superficial = false`, CA-SL-11), a `.tt` ALLOWLOSS filing position and an in-kind contribution's loss (denied for good by s.40(2)(g)(iv), not by this rule) were all said to have no replacement on day 30.
- **Fix:** upgrade. The reason is the one the books show: the exemption, your ALLOWLOSS line and its reason, the in-kind contribution, or "no units were held at the end of day 30 (<date>)" counted from the books; otherwise "no denial applied by the engine", with the units held on day 30. Nothing the return reports changes.
- **Fixed in:** `v0.36.1`
- **Code:** `src/taxjson/lib/edge_cases.py` — `windows_across_year_end`, `_allowed_why`, `allowed_by`, `buyback_superficial`

### `tjs sum --json`: `totals.realized` is 0.00 while `filing.totals.gain` holds the year's gain (or `tjs sum` prints a REALIZED total that is not the FOR THE RETURN total)
- **Check:** `work/<account>_report.json` is newer than the account's gains file and its `"gains"` object lacks `ticker_stats` (an empty `{}`: a truncated, interrupted or hand-edited file). Since the fix: "Warning: work/margin_report.json holds no usable gains aggregate — margin's row is recomputed from margin_gains.json".
- **Cause:** `sum` reads the account's report file (the run's pre-computed aggregate) instead of re-summing the gains file when it is at least as fresh; it checked the file's version and basis, not that the aggregate was there, so an empty one counted as no gains. The FOR THE RETURN block reads the gains file itself, so one document carried two totals.
- **Fix:** a full `tjs run` rewrites the report file. Since the fix, `sum` requires the aggregate's `ticker_stats` and `total_fees` before serving it and otherwise recomputes the row from the gains file, with the Warning above: both totals come from the same books.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_summary`, `_report_aggregate_usable`; `src/taxjson/lib/report_model.py` — `build_account_report`

### `tjs sum`: a futures close's gain is in the "ASSET: Non-option" statistics block, its fee in a "Futures" block
- **Check:** `tjs sum --json`: `returns_by_asset.Stocks` has the futures gain, `returns_by_asset.Futures` only its fee.
- **Cause:** the returns and the fees used different classifiers.
- **Fix:** upgrade: one Futures block, with the fee per contract.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_sum_gains.py` — `summarize_gains`, `output_statistics`

### `tjs sum`: the Options statistics block pairs a buy-back with another account's write
- **Check:** two accounts wrote the same series under grant timing; the per-trade results are not each account's own write minus its buy-back.
- **Cause:** the open writes were keyed by series and currency, not by account.
- **Fix:** upgrade.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_sum_gains.py` — `summarize_gains`

### `tjs sum`: "! FX on foreign cash (line 15300): not in the rows; its estimate is NOT RELIABLE — tjs fx-cash", or `tjs fx-cash`: "FX on foreign cash: NOT RELIABLE for 2025 — 3 in-year overdrafts; do not file this figure."
- **Check:** `tjs sum --details` and `tjs fx-cash --details` give the whole line: "FX on foreign cash: NOT RELIABLE for 2025 — 3 in-year overdrafts (412.50 USD); conversions, deposits/withdrawals and margin balances are not read; do not file this figure"; `tjs fx-cash --json` has `"reliable": false` and the raw figures under `unreliable_raw`; `tjs checklist` shows "! 38 fx-cash: FX on foreign cash: NOT RELIABLE for 2025 — 3 in-year overdrafts... — tjs fx-cash". The project has no `[settings] fx_cash_ledger`, or it is `"v1"`.
- **Cause:** the default FX-on-cash ledger (s.39(1.1), US §988) is rebuilt from the trades and income of the taxable books only. It never sees a currency conversion (IB Forex trades, Kraken or Coinbase fiat trades, a bank's), a deposit or withdrawal, a foreign-currency margin balance or the pool carried from the year before, so each spend it cannot cover is an "overdraft" moved at the day's rate with no gain, and the result can be wrong in either direction. Before this release `tjs sum` showed its figure as "reportable … line 15300" with a caveat.
- **Fix:** do not file that figure. Work out the line 15300 FX result from the statements, or try the opt-in ledger v2 (under audit): `tjs fx-cash --ledger v2` reads the conversions, deposits/withdrawals and statement balances, and says what it computed or, line by line, what it needs (next entry). `tjs checklist --done fx-cash --force --note "..."` records that you handled it yourself (shown `[f]`; on a release where the plain mark was taken and the step stayed open, see "`tjs checklist --done fx-cash` prints …").
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/bin/taxjson_fx_cash.py` — `unreliable_status`, `NOT_READ`; `src/taxjson/bin/taxjson_run.py` — `_fx_cash_status`, `_fx_sum_item`, `cmd_fx_cash`; `src/taxjson/lib/checklist.py` — `d_fx_cash`

### `tjs fx-cash --ledger v2`: "FX on foreign cash: NOT COMPUTED for 2025, ledger v2 (opt-in, under audit) — 4 problems, first: no opening pool for margin/ib USD … ; no reportable figure"
- **Check:** the report's NOT COMPUTED table lists each problem with its date, account (book) and amount, and the lines below it say what to add; `--cash-events` lists every conversion, move and balance the ledger read.
- **Cause:** ledger v2 refuses instead of guessing: an account holds foreign cash at the start of the year with no cost (`opening`), a deposit or withdrawal nothing declares (`undeclared`), a statement balance the ledger does not reach within 1.00 (`reconcile`: a conversion or move it does not see), an account with activity and no balance to check against, an overdraft in an account that does not reconcile, a move between your own accounts that arrives before it leaves (`own`), a missing FX rate, or an account of a broker whose export it reads no conversion, deposit or withdrawal from (`unread`: Webull, the generic importer).
- **Fix:** add the `.tt` lines it names to a `.tt` file of the account's `inputs/` folder (docs/settings.md, `.tt` files: `CASHOPEN` once for the first year, `CASHMOVE … cost=` / `kept` / `proceeds=` / `own` / `spot` per move, `CASHBAL` for an export without balances, `CASHBOOK <book>` in a `.tt` file of a folder holding several broker accounts, `CASHBOOK <book> complete` once a Webull or generic account's conversions and moves are all lines), then `tjs fx-cash --ledger v2` again; `fx_cash_inflow_cost = "spot"` takes the day's rate for undeclared deposits. Close the year with `fx_cash_ledger = "v2"` and the next year opens from the recorded pool.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/fx_cash_v2.py` — `build`, `headline`, `TOL`; `src/taxjson/lib/cash_events.py` — `parse_line`, `collect`, `Books`

### `tjs fx-cash --ledger v2 --cash-events` lists a deposit twice (the broker's move, then the `.tt` `CASHMOVE` that declared it), or prints no CASH EVENTS READ section at all
- **Check:** the ACQUIRED total counts the deposit once; the second line is the `.tt` line, with no broker description; with nothing read, the output is the same with and without `--cash-events`.
- **Cause:** the list showed the `.tt` lines that declared a broker's move as moves of their own, and an empty list printed nothing (backlog TJ-0054, TJ-0168).
- **Fix:** upgrade: a declared move is listed once, with the line that declared it under DECLARED BY, and an empty list says "None read from the exports or .tt lines." Nothing changes in the figure or in `--json`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/fx_cash_v2.py` — `render`, `DECLARED BY`

### `tjs fx-cash --ledger v2` computed a Questrade account's figure as a margin loan ("BORROWED"), or: "wb/webull: taxjson does not read webull's conversions, deposits or withdrawals from its export"
- **Check:** `tjs fx-cash --ledger v2 --cash-events` lists what the ledger read for the account; the report's NOT READ FROM THE EXPORT table names the accounts whose exports give it nothing.
- **Cause:** ledger v2 read no cash event from a Questrade, Webull or generic export, so an account that converted Canadian dollars before buying a US share read as one that borrowed them, and a figure was computed on it.
- **Fix:** upgrade: Questrade's FX conversions (FXT), deposits, withdrawals, cash-only transfers and stock-lending income are read. For Webull and the generic importer write each conversion and move as a `.tt` line (`FXCONV`, `CASHMOVE`) in the account's folder, then `CASHBOOK <book> complete` (e.g. `CASHBOOK webull complete`) to say they are all there.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/brokerages/questrade.py` — `questrade_cash_events`; `src/taxjson/lib/cash_events.py` — `READERS`, `unread`; `src/taxjson/lib/fx_cash_v2.py` — `unread_books`

### `tjs fx-cash --ledger v2`: the figure changes when a `CASHMOVE … own` arrives a few days later
- **Check:** two runs that differ only in the arrival date of a move between your own accounts (`CASHMOVE <date> USD 1000 own` in the receiving account) give a different "net" and a different year-end pool (`tjs fx-cash --ledger v2 --json`, `pools_year_end`), with a conversion or spend of the same currency in another account between the send and the arrival.
- **Cause:** ledger v2 took the units and their cost out of the pooled average cost when they left one account and put them back only when the other recorded them, so a disposition in between was costed on a pool without them. Currency in transit between your own accounts is still yours and stays in the pool.
- **Fix:** upgrade: the units and their cost stay in the pool while in transit (the ledger tracks only where they are, to match the arrival and reconcile each account); an arrival in an account with a negative balance repays that debt at the pool's average cost. Ledger v2 stays opt-in and under audit.
- **Fixed in:** `v0.29.1`
- **Code:** `src/taxjson/lib/fx_cash_v2.py` — `build`, `transit`, `arrive`

### `tjs fx-cash --ledger v2`: a `CASHMOVE` declaration in a file with a `CASHBOOK` line is shown on another broker account's move, and its own move is "undeclared"
- **Check:** one account folder holds two broker accounts with an identical move on one day; the `.tt` file holding the declaration names its book with `CASHBOOK`, and `tjs fx-cash --ledger v2 --json --cash-events` shows `decl_where` on the other book's move.
- **Cause:** the declaration was held to a book only when it had its own `at=`; the file's `CASHBOOK` line, which settings.md says scopes its lines, was ignored when matching (2026-10 audit #85).
- **Fix:** upgrade: a declaration matches only moves of its line's book, from `at=` or the file's `CASHBOOK` line. On an older release, add `at=<book>` to the `CASHMOVE` line.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/fx_cash_v2.py` — `build`

### `tjs fx-cash --ledger v2`: an IB internal transfer out of one account and into another shows no `CASHMOVE`, with reconcile gaps and overdrafts on both books, when both accounts' statements are named alike
- **Check:** two taxjson accounts each hold an IB statement of the same file name (`activity.csv`) with an Internal Transfer of the same amount on the same day; renaming one file changes the result.
- **Cause:** the pairing that drops the two legs of a transfer inside one combined statement matched on the file's base name only, so legs in two accounts' statements paired and both vanished (2026-10 audit #60).
- **Fix:** upgrade: legs pair only inside one statement (its account folder and file). On an older release, give the statements different file names.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/cash_events.py` — `pair_internal`

### `tjs fx-cash --ledger v2`: a move between your own accounts that leaves and arrives on one settlement day is refused twice — "came in … as a move between your own accounts, but only 0.00 USD had left one of them before it" and "100.00 USD left an account as a move between your own accounts and never arrived in another"
- **Check:** `tjs fx-cash --ledger v2 --cash-events` shows the two `CASHMOVE … own` legs with one settlement date and the arrival's trade date BEFORE the departure's (the receiving broker books the day you asked for the move, the sending one the day it moved).
- **Cause:** the events of one settlement day were walked in trade-date order, so the arrival came before the departure it matches and found nothing in transit (2026-10-24 audit A20).
- **Fix:** upgrade: on one settlement day an arrival that finds too little in transit is walked with the departure of its currency later that day: the departure comes up to the earliest point (no earlier than the arrival) from which its account holds the units until its own place in the day, and the arrival right after it; the rest of the day keeps its trade-date and time order, so a conversion that funds the departure that day still comes first. (Moving every departure to the start of its day instead, as the first fix did, overdrew the account a same-day conversion funds: a BORROWED row, a gain ON DEBT and the pool at the day's rate, not the amount paid; 2026-10-25 audit diff-engine#2.) On an older release, give the arrival's `CASHMOVE` line a trade date on or after the departure's.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/fx_cash_v2.py` — `_order`, `_waits`

### `tjs fx-cash --ledger v2`: "came in on … as a move between your own accounts, but only 0.00 USD had left one of them before it" when the receiving broker books the move a day or two before the sending one; or a chain of own moves on one day (a to b to c) shows BORROWED on the middle account and a gain on debt
- **Check:** `tjs fx-cash --ledger v2 --cash-events` shows the arrival's `CASHMOVE … own` with an earlier settlement date than the departure's; for a chain, the middle account's departure dated before its arrival on the same settlement day, and a BORROWED row for it.
- **Cause:** an arrival was matched only with a departure of the same settlement day, and for a chain the middle account's own departure was walked before the arrival it passes on, so it overdrew (backlog TJ-0067).
- **Fix:** upgrade: an own arrival with too little in transit is walked on a later departure: its own account's later that day (the units pass through it), or another account's that settles within 7 days while that account holds the units until they leave; the units stay in the pool, so no figure moves. One further apart is still refused, and the message says why ("… settles more than 7 days after it", "… does not hold them until then"): check the two `CASHMOVE` lines' settlement dates, or declare it `cost=` / `spot`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/fx_cash_v2.py` — `_promise`, `_waits`, `OWN_EARLY_DAYS`

### `tjs fx-cash --ledger v2`: "NOT COMPUTED … filed/2025.json: the prior-year record cannot be read", "… margin/ib USD carries {'units': 100, 'cost': nan} — units and cost must be finite numbers", "… margin/ib USD carries 100.00 USD at a cost of -5.00 CAD — as on a CASHOPEN line, the cost is a positive base-currency amount …", "… was closed under country = "canada" (Canada law) but this project is country = "usa" … its foreign-cash pool (costs in CAD) cannot open a United States year", or "… its costs are in CAD, and this project's base currency is USD"
- **Check:** the NOT COMPUTED table lists the problem with the record it read (`filed/<year-1>.json`, or the file `[settings] prior_year_record` names); `--json` shows it as a `carry` problem.
- **Cause:** the prior year's close (`fx_cash_v2` in the close-year record) is this year's opening pool. A record that exists but cannot be read, is not a close-year record, carries another year's close, was closed under the other country or whose costs are in another base currency used to read as NO record, and a unit count or cost that is not a finite number was carried as it was; the ledger then computed, and certified as reliable, a year opened on an unknown or wrong pool (2026-10-24 audit C02, C03, C04). A carried cost below zero, or a cost on no units, which a `CASHOPEN` line is refused for and a close never writes, was carried too (a pool of 100 USD at -5 CAD gave a gain on nearly all of it; 2026-10-25 audit year-end#2): a carried book is now held to the `CASHOPEN` line's rule. Only an absent record, or one closed under the default ledger, means no carry.
- **Fix:** restore the record from git, or re-close the prior year with `fx_cash_ledger = "v2"` (`tjs close-year --force` in that year's project, only if that year's figure is right). A record of the other country or base currency cannot open this year: if this project's `country` / `base_currency` is the wrong one, fix it; else open the year with `CASHOPEN` lines (docs/settings.md).
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_fx_cash_carry`; `src/taxjson/lib/fx_cash_v2.py` — `_carry_openings`, `_finite`; `src/taxjson/lib/cash_events.py` — `opening_problem`

### `tjs fx-cash --ledger v2`: "… was closed under country = "canada" (Canada law) but this project is country = "usa" … else open the year with CASHOPEN lines (docs/settings.md)" stays after the `CASHOPEN` lines were added, or for a prior-year record closed under the default ledger
- **Check:** the OPENING table lists your `CASHOPEN` lines, and the problem remains; or `filed/<year-1>.json` has no `fx_cash_v2` key (the year was closed with the default ledger).
- **Cause:** the record's country was checked before its pool was looked for, and the `CASHOPEN` lines the refusal asks for never cleared it (backlog TJ-0022).
- **Fix:** upgrade: a record with no `fx_cash_v2` pool is no carry, whatever its country; with `CASHOPEN` lines a record of the other country (or of another base currency) is a note ("… is not carried into this United States year, which opens from its CASHOPEN lines"). Without them the year is still refused.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_fx_cash_carry`; `src/taxjson/lib/fx_cash_v2.py` — `_carry_openings`

### `tjs fx-cash --ledger v2`: "no opening pool for a/a USD — add `CASHOPEN 2025-01-01 USD <settled units> <cost in CAD> at=a` (0 0 when it held none) to a .tt of a, or close 2024 with this ledger" every year, for an account that ended the prior year at zero, although that year was closed with ledger v2
- **Check:** `filed/<year-1>.json`'s `fx_cash_v2` `books` leaves out the account the message names; the account has no statement balance dated the prior December 31 (a `.tt`-only book, Questrade).
- **Cause:** the close recorded only the books that ended the year holding or owing the currency, so a book that ended at zero had no opening the next year, and the message asked for a close that had already been made (backlog TJ-0028).
- **Fix:** upgrade: the close records every book the year walked, at zero when it ended there. A record written by an older release still lacks it: add the `CASHOPEN … 0 0` line once (the message now says "the close of 2024 does not list it: it held none then, or is new"), or re-close that year with `tjs close-year --force` in its project.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/fx_cash_v2.py` — `build`, `does not list it`, `_carry_openings`

### `tjs fx-cash --ledger v2`: a second account of the same broker appeared in the account folder and the year-end pool doubled (`opening` lists both `margin/questrade USD` from `close of 2025` and `margin/questrade:3f2a USD` from the `CASHOPEN`); or "close of 2025 carries 100.00 USD (cost 140.00 CAD) for margin/questrade, and margin now holds 2 questrade accounts (…) where it had one — which of them held it the record does not say"
- **Check:** `tjs fx-cash --ledger v2 --json`: `opening` has an entry whose source is `close of <year-1>` under a book name without an account suffix beside entries with one, and `pools_year_end` is twice what the accounts hold.
- **Cause:** a broker's book is named by its account (`margin/questrade:3f2a`) only while the folder holds several accounts of that broker, so the book was renamed when the second account's export arrived; the balance carried under the old name matched none of this year's books, and a `CASHOPEN` for the right one was ADDED to it instead of replacing it (2026-10-24 audit C05).
- **Fix:** upgrade: a `CASHOPEN` for one of the broker's books replaces the carried balance (a note says so); without one the carry is refused with the `CASHOPEN` line to add, rather than opened under a book no account has. A close written by this release records each book's broker account, and a later year places the carried balance by it (or by the account prefix in the book's name) when the names change again.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/fx_cash_v2.py` — `_carried_book`, `_carry_openings`, `_book_account`; `src/taxjson/lib/cash_events.py` — `Books`, `book_name`

### `tjs fx-cash --ledger v2`: "NOT COMPUTED … margin/cash.csv: its cash events could not be read (BrokerageParseError: cash.csv line 2: Net Amount 'broken' is not a number this parser accepts … — a DEP cash row the FX-on-cash ledger reads; fix the cell in the export)"
- **Check:** open the Questrade export at the line named: a deposit, withdrawal, contribution, FX conversion, cash-only transfer or stock-lending row whose Quantity or Net Amount is not a number (an edited cell, a note, `N/A`).
- **Cause:** the ledger reads these cash rows from the export (the position parser counts them as non-events without reading their amount), and an amount it could not read used to drop the row in silence, so the ledger computed, and certified, a year without that deposit or conversion (2026-10-24 audit C01). A row of any other kind with such a cell is refused by `tjs run` as before.
- **Fix:** correct the cell in the export (download it again if the broker's file is sound), then `tjs fx-cash --ledger v2`; or remove the row and declare the move as a `.tt` `CASHMOVE` / `FXCONV` line (docs/settings.md).
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/brokerages/questrade.py` — `questrade_cash_events`; `src/taxjson/lib/cash_events.py` — `collect`

### "Error: m.tt:4: malformed CASHBAL line — … — a line of the FX-on-cash ledger v2 only"
- **Check:** the line named is a `FXCONV`, `CASHMOVE`, `CASHOPEN`, `CASHBAL` or `CASHBOOK` line.
- **Cause:** the cash lines are read only by the opt-in ledger v2, but a malformed one stops `tjs run` under either ledger (a typo must not vanish).
- **Fix:** correct the line to the form the message shows, or delete it if you do not use ledger v2.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/bin/taxjson_convert_tt.py` — `a line of the FX-on-cash ledger v2 only`

### `tjs checklist`: "! 14 journals: 1 pending journal(s) between two listings (1 suggested, 0... — tjs journals --pending"
- **Check:** `tjs checklist --all` shows the whole line ("1 pending journal(s) between two listings (1 suggested, 0 refused)"); `tjs journals --pending` lists each journal the books did not pool: its date, FROM → TO, quantity, broker, how it was found (a Questrade BRW journal, an RBC journal transfer, an IB InterDepot, a move across brokers), the reason, and the lines that settle it. `tjs journals` shows the joined ones too, each with the `TOBASE` / `JOURNAL` line that pools it.
- **Cause:** a broker journal moved a position from one listing of a security to another, and the run did not join the two listings: their names are not equal word for word or the legs pair more than one way (suggested), or the two legs name different companies (refused). Not pooled, the old listing's shares never sell and the new listing's sales read as a short.
- **Fix:** if they are one security, add the `.tt` line (`JOURNAL <date> FROM TO QTY`, in a `.tt` file of the account's `inputs/`) or the ticker.map line it gives, then `tjs run`. If the legs really are two securities, add `DISTINCT FROM TO` to ticker.map: a journal your ticker.map keeps apart (a `DISTINCT` line, a line naming a listing) is still listed as refused, with how to change it, but is a decision made and never pending.
- **Fixed in:** `v0.24.0`
- **Code:** `src/taxjson/lib/checklist.py` — `d_journals`; `src/taxjson/lib/journals.py` — `report`, `_classify`; `src/taxjson/lib/cross_listings.py` — `analyze`, `refused`

### `tjs journals --pending` says nothing is pending (exit 0) and `tjs checklist` has step `journals` done, although `work/cross_listings.state` is damaged
- **Check:** `work/cross_listings.state` does not read as JSON (truncated by an interrupted run, or edited), or its `"format"` is not `cross_listings/1`. Since the fix: "Error: work/cross_listings.state is not JSON (…) — run `taxjson run` first" (or "has format …, not 'cross_listings/1'"), and the checklist step is blocked with the same words.
- **Cause:** the state reader answers a missing, unreadable or foreign-format file with empty record lists, so no reader can crash on it; `tjs journals` and the checklist took those empty lists as "no journals", not as "no record".
- **Fix:** `tjs run` rewrites the file. Since the fix, the journals report refuses a state file it cannot read and names why, and the checklist step stays blocked until a run rewrites it.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/cross_listings.py` — `state_problem`, `read_state`; `src/taxjson/lib/journals.py` — `report`, `JournalsError`; `src/taxjson/lib/checklist.py` — `d_journals`

### `tjs checklist`: "! 15 renames: 1 look-alike rename(s) not booked — tjs renames --pending"
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

### A full `tjs run` opens with "Warning: margin_gains_wash.json was built before margin_base.json changed — `taxjson run`", one per taxable account, after a `tjs run --account`
- **Check:** the warnings come under "==> Checking the project" of the full run that rebuilds those files; the commands after it say nothing.
- **Cause:** a full run first keeps the reports it is about to replace as `tjs compare`'s `prev` snapshot, and reading those books' figures said they were stale — to the run rebuilding them.
- **Fix:** nothing to do. Since the fix the snapshot reads them quietly; `tjs sum` and the other readers still warn while the books are stale.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/compare.py` — `rotate_previous`, `warn_stale`; `src/taxjson/lib/report_model.py` — `resolve_gains_files`, `warn_stale`

### No "wash-adjusted numbers are stale" warning after a NEW account was added and built with `tjs run --account <new>`
- **Check:** `work/.wash_inputs.json` was written by the last full `tjs run`; the new account's `work/<new>_base.json` and `<new>_gains.json` are newer than every `<account>_gains_wash.json`, and `tjs sum`, `tjs checklist` and `tjs close-year` say nothing about them.
- **Cause:** a taxable account's wash-adjusted numbers come from one pass over every taxable account, and the record of that pass names the books it had. The stale check compared the recorded books and stopped there: an account the pass never saw — added since, built alone with `--account` — was no recorded input, so its books could not make the result stale, and a replacement it bought (or identical property it holds, Canada) never reached the served numbers.
- **Fix:** run a full `tjs run` (no `--account`) after adding an account, before using or filing any figure. Since the fix, the pass records the roster of accounts whose books existed when it ran, and an account outside it whose books are newer than the wash file marks the result stale (the same warning, checklist attention and close-year stop). A record an older release wrote has no roster: until the next full run, a sheltered account rebuilt alone can warn too.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/report_model.py` — `stale_wash_inputs`, `record_wash_inputs`, `_book_accounts`

### "Warning: these books are not the clean result of the current inputs"
- **Check:** `tjs checklist` step `run-clean` names the problem: validation errors in the last run, an account deferred on elections, inputs newer than the books, or a run that did not finish.
- **Cause:** a report command (sum, list, form-export, …) reads the books in `work/`, and those no longer match `inputs/` and taxjson.toml. `tjs find-missing-history`, `tjs sanity` and `tjs transfers` read them too and say it as well (before this release they said nothing: backlog TJ-0077).
- **Fix:** fix what it names and re-run `tjs run` before using any figure.
- **Fixed in:** `v0.17.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_warn_run_state`, `_run_state_problems`, `_RUN_STATE_BANNER_CMDS`

### "Error: --write-missing-history: the books in work/ are older than the inputs (changed since the last full run: inputs/margin/z.tt)" (or "--write-purchases: …"); on an older release the lines were written, an `OPENING … cost=unknown` line for a position the new purchase fixes
- **Check:** the inputs changed after the last `tjs run` (a `.tt` purchase just added, a new export); `tjs checklist` step `run-clean` says "inputs changed since the last full run". Nothing was written: `inputs/<account>/missing_history.tt` and `purchases_draft.tt.txt` are as they were.
- **Cause:** the write flags draft their lines from the books in `work/`, which hold the last run's inputs. Run right after a purchase was added to a `.tt` file, `--write-missing-history` wrote an unknown-cost opening for the sale that purchase covers: the position opened twice, and the run then reported the line as stale. Now both flags refuse until the books are rebuilt. The files the write changes itself do not count (`missing_history.tt`, and taxjson.toml, where it pins `requires_taxjson`): a second write before a run still merges with the first.
- **Fix:** `tjs run`, then the command again. An `OPENING` line written by an older release in that case: `tjs find-missing-history` lists it under STALE; delete it.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_refuse_stale_books_write`, `are older than the inputs`, `cmd_find_missing_history`

### "Warning: these books are not the clean result of the current inputs (2 problems)" for one change of inputs; `--details` lists "- inputs changed since the last full run (changed: inputs/margin/a.tt" and "- added: inputs/margin/b.tt)"
- **Check:** `tjs sum --json` has the two halves in `run_state_problems`, with unbalanced parentheses, and two `run-state` issues in `readiness`.
- **Cause:** the report commands read the problems back by splitting the checklist's run-clean finding at "; ", which also split the list of changed and added files inside one problem.
- **Fix:** nothing to do: the figures are the same. Since the fix it is one problem.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_run_state_problems`; `src/taxjson/lib/checklist.py` — `d_run_clean`, `problems`

### Crypto only: `tjs gains` says "Error: no native gains files in work/ — run `taxjson run` first" after a run that succeeded
- **Check:** the line above it is "Info: crypto account 'crypto' is not shown (crypto has no native-currency gains)", and the exit is 1.
- **Cause:** crypto has no native-currency gains file, so a project whose only books are crypto has none, and the view asked for a run that cannot make one.
- **Fix:** the gains are in `tjs sum` and `tjs winners`. Since the fix `tjs gains` stops after the `Info:` line with exit 0.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_gains`, `crypto has no native-currency gains`

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

### `reports/ib_wash.sum` DIAGNOSTICS: "[margin] warning: ATTENTION: inputs/margin/hand.tt:1 lists …" (older releases: the same line without `[margin]`, as if it were about ib)
- **Check:** the line names a file of another account (`inputs/margin/…`); `reports/margin_wash.sum` has it too, without the tag.
- **Cause:** the blended pass (ACB pooled across the taxable accounts, superficial losses across them) runs once over every taxable account, and its messages go into every blended account's `<account>_wash.sum` DIAGNOSTICS. A message about one account's file was copied into the others with nothing saying whose it was. It now starts with that account's name in brackets in the other accounts' banners; a message about the whole blend, or about files of two accounts, stays as it is, and nothing is left out.
- **Fix:** act on it in the account it names (here `inputs/margin/hand.tt`, line 1), then `tjs run`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `collect_diagnostics`, `_diag_block_account`, `_DIAG_INPUTS_RE`, `stage_blended_wash_pass`

### A futures close's gain in `tjs sum` (Schedule 3) is not the P/L its broker, its T5008 or `<account>.sum` shows, with "Info: F:QZZ6.US traded in more than one TAXABLE account"
- **Check:** `tjs audit` shows the close under "ACB pool — ITA s.47, blended across taxable accounts"; another taxable account held the same contract (the same symbol) in the same direction when it closed.
- **Cause:** Canada averages identical property across all your taxable accounts (s.47): a close is against the average cost of the contract's open positions of that direction in every taxable account (`CA-FX-04` in `tjs tax-logic --ids`); the broker only knows its own account. taxjson v0.29.0 and older took each account's own average, so moving contracts between your accounts changed the gain, and a short in one account and a long in another were netted, which moved a close's P/L into the year the other position closed (issue #76). A rare "note: F:QZZ6.US: held in several taxable accounts, but its settled rows do not record their native amounts" means books written by an older taxjson: each account's own average was used.
- **Fix:** nothing to do for this year: file from `tjs sum` or `tjs form-export`. `tjs run` rebuilds books from an older taxjson. If you filed an earlier year with one contract in two taxable accounts, `tjs check-filed` shows whether its figure moved.
- **Fixed in:** `v0.29.1`
- **Code:** `src/taxjson/lib/futures.py` — `pool_across_accounts`, `engine_pool_key`, `held in several taxable accounts`; `src/taxjson/lib/core.py` — `_fold_short_futures_pools`

### `tjs wash-sales` denies a loss whose replacement arrived by a rename or merger, and the later sale's gain in `tjs sum` never gets the denied amount back
- **Check:** `tjs audit QZA.TO` for the loss's symbol: the loss sale is denied (deferred, not "permanently"), the symbol held no shares right after it, and a rename or merger (a broker corporate-action row, a `.tt` `RENAME` or `SPLIT ... QZB.TO QZA.TO` line) later brought the replacement shares in under that symbol. On v0.29.2 and older the ACB of those shares after the rename is their own cost only, without the denied loss; once they are sold the total gain exceeds proceeds less purchases by the denied amount. `tjs sanity` and the holdings check agree, since the share counts are right.
- **Cause:** a denied loss is added to the replacement's ACB (CA-SL-09). When the loss's symbol holds nothing at that moment, the amount waits until the symbol next opens a position. A purchase applied it, but a rename or merger into the empty symbol did not, so the loss was denied and never recovered (issue #91; CA-SL-19 in `tjs tax-logic --ids`). The US engine adds a wash sale's disallowed loss to the replacement lot itself, which the rename carries, and was not affected.
- **Fix:** upgrade, then `tjs run`: the renamed shares' ACB now includes the denied amount, and each later sale (partial or final) recovers its share. If you filed an earlier year with such a sale, `tjs check-filed` shows whether its figure moved.
- **Fixed in:** `v0.30.0`
- **Code:** `src/taxjson/lib/core.py` — `CanadaTaxRules`, `pending_wash`

### `tjs wash-sales` shows a loss "permanently denied"
- **Check:** `tjs wash-sales` lists each denied loss, its trigger and DENIED vs ALLOWED; `tjs audit` traces the sale.
- **Cause:** identical property was bought within 30 days of the loss and still held at day 30 in a registered account (or by an affiliated person), so the loss is gone from your return (s.54, s.40(2)(g)(i)). A denial against a taxable repurchase is added to that ACB instead (s.53(1)(f)).
- **Fix:** check each trigger is real (the right account, the same security); a mis-assigned account type or ticker is the usual mistake. The rule ids are in `tjs tax-logic --ids` (CA-SL-*).
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_wash_sales`; `src/taxjson/lib/core.py`

### `tjs wash-sales --explain` traces a denial and still says "Info: no matching gains found"
- **Check:** the trace above the line shows the superficial loss (US: the wash sale); the project has a crypto account (or, in the US, more than one), whose book is traced on its own.
- **Cause:** each book was explained by its own call, and a book with no denial (the crypto one) printed the note under the other book's real trace.
- **Fix:** nothing to do: the trace is right. The note now appears once, only when no book has a denial.
- **Fixed in:** `v0.28.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_explain_wash_sales`; `src/taxjson/bin/taxjson_explain.py` — `main`

### `tjs wash-sales --explain`: "deferred …: added to the ACB of the replacement units in your taxable pool (recovered when they are sold); first bump +… on DATE" names the registered account's purchase
- **Check:** the same block says "PERMANENTLY denied …: the replacement is held in a registered account"; the window table lists a `trigger, permanent [sheltered]` row on that date and a taxable `trigger` row later. `tjs list` shows the deferred amount on the taxable replacement.
- **Cause:** the "first bump" was the first trigger's amount and date, here the registered account's part, which is denied for good with no ACB addition (backlog TJ-0096). The figures were right.
- **Fix:** upgrade: a denial that is part permanent names the first taxable replacement's date and account (`…, on 2024-08-20 (margin2)`); the permanent part has its own line.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/trace_format.py` — `_render_wash_explanation`

### `tjs wash-sales --explain`: the window table's ACB/SH on the trigger row leaves out the deferred loss the text says was added on that date (`tjs list` shows a higher ACB per share)
- **Check:** the legend says "ACB/SH: the taxable pool's ACB per unit after the row"; the trigger row's figure is the pool's before the denied loss, and the next row (or `tjs list`) has it.
- **Cause:** the window rows took the pool after the trigger purchase itself; the denied loss is added right after that row (a replacement bought before the loss: right after the loss sale), so the row showed the pool without it (backlog TJ-0111).
- **Fix:** upgrade, then `tjs run`: the row the addition lands after shows the pool with it.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/core.py` — `_bump_after`, `acb_per_share_after`; `src/taxjson/lib/trace_format.py` — `_report_window_lines`

### `tjs audit SYMBOL`: the pool history prints an ADJUST's amount under QTY (`2024-09-16  ADJUST  50.0000  -  0.0000  50.0000  22.0000 …`)
- **Check:** POOL_QTY does not change on the row; the amount is the same under COST+/-.
- **Cause:** the engine's trace line for an ADJUST gives its amount where other rows give the quantity, and the table read it as one (backlog TJ-0113).
- **Fix:** upgrade: an ADJUST row shows `-` under QTY; its amount stays under COST+/-.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/trace_format.py` — `_trace_table`

### `tjs form-export`: a short sale's row shows PROCEEDS net of its commission with OUTLAYS 0.00, while other sales show the commission as an outlay
- **Check:** the row is a short sale (the note "includes short position(s)"); PROCEEDS is what the short sale brought in after its commission, and the gain is right.
- **Cause:** the opening commission of a short sale is not on the closing row the form is built from, so it stays netted in the proceeds (as a close-timing write's does). The notes of `tjs sum --details` and `tjs form-export --details` said sell-side commissions are outlays, without the exception (backlog TJ-0119).
- **Fix:** upgrade: both notes say a short sale's opening commission stays netted in PROCEEDS. The figures are unchanged: the gain is the same either way; line 13199 (10689 for Period 1 of 2024) is lower by the commission than a broker slip's gross proceeds.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_form_export.py` — `short sale's opening commission`; `src/taxjson/bin/taxjson_run.py` — `cmd_summary`, `stays netted`

### `tjs audit` right after a `tjs run` that stopped at a corp-action election: "pipeline tie-out 3 tied, 3 MISMATCHED" and "CHECKS FAILED … The usual cause is stale work/ artifacts; re-run `taxjson run`"
- **Check:** `tjs elect --pending` lists the election; `work/<account>_pending_elections.json` exists. Running `tjs run` again without deciding it gives the same result.
- **Cause:** a deferred account stops the run before its cross-account pass, so the other taxable accounts' saved gains are their own, unblended figures; the audit recomputes them blended across your taxable accounts and could not tie them (backlog TJ-0196).
- **Fix:** upgrade: the accounts blended with a deferred one are not audited either, and the audit says so: "Warning: not audited — corp-action elections pending for margin (so ib, questrade, webull too: no cross-account pass)" (exit 1; crypto accounts audited on their own are still audited). Decide the election (`tjs elect --pending`), then `tjs run` and `tjs audit`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_audit`, `_held_back`, `_pending_head`

### `tjs run` takes many minutes and `work/<account>_gains.traces` (and `work/.blend_gains_wash.traces`) grow to gigabytes for a symbol traded thousands of times
- **Check:** `ls -l work/*.traces work/.*.traces`: files of hundreds of MB or more; in one, every sale's block starts again at `# --- ACB CALCULATION TRACE: SYMBOL ---` and repeats every earlier line of the pool (4,000 rows of one symbol: two files of about 890 MB; 8,000 rows: 3.5 GB each).
- **Cause:** the Canadian engine copied the symbol's whole pool history into the trace of every sale, so the traces (memory, and the files `tjs run` writes) grew with the square of the symbol's sales (audit 2026-10-25 robustness#5).
- **Fix:** upgrade, then `tjs run`: each sale's trace shares the pool's history instead of copying it, and in a traces file a sale's block shows the pool lines since the block above of the same pool, from the pool after that sale on, headed `# --- ACB CALCULATION TRACE: SYMBOL (continued from the sale of DATE above, id=ID) ---`; every pool line is written once (8,000 rows: 4.4 MB). `tjs audit` and `tjs wash-sales --explain` still show a sale's whole pool history. On v0.35.0 a year with thousands of loss sales still takes minutes: the superficial-loss check (the next entry).
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/trace_format.py` — `SharedTrace`, `continued_header`; `src/taxjson/lib/core.py` — `SharedTrace(symbol_acb_traces[symbol])`; `src/taxjson/bin/taxjson_gains.py` — `write_traces_file`, `_since_shown`

### `tjs run` sits for minutes on "==> Calculating capital gains" and "==> Pooling cost and checking superficial losses across taxable accounts (…)" for an export of thousands of trades
- **Check:** the run ends and its figures are right, only slowly, and the time grows faster than the rows: on v0.35.0, 8,000 rows of one symbol took about 2.5 minutes, 10,000 rows of 40 symbols about 1.5 minutes, 50,000 rows about half an hour.
- **Cause:** for every loss sale, the Canadian engine's superficial-loss check walked every row of the books — the purchases inside the ±30-day window, the calls on the shares, what was still held at day 30, the window listing — and every denial it had made, again in each pass, so a run cost loss sales x rows (TJ-0105, TJ-0060). Reading the row model's fields again for every row loaded added a third more.
- **Fix:** upgrade, then `tjs run`. The check reads indexes of each security's rows by date window, with running sums of what each account holds; they give the walk's own rows in the walk's order, so no figure changes. On the same machine: 8,000 rows of one symbol 14 s, 10,000 rows of 40 symbols 15 s, 50,000 rows 78 s. A symbol bought back and still held after each of thousands of loss sales still takes longer (each pass compares a loss with the purchases in its own window), and there is no progress line: a long step shows only its `==> ` line.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/core.py` — `_DatedRows`, `_ClassBalances`, `_WashAdjustIndex`, `_iso_ordinal`, `_dis_by_id`, `_tx_field_names`

### `tjs sum --other-income …` (or `tjs estimate`): the estimate's "[512.34 realized …]" is a cent off the RETURN row's GAIN (512.33)
- **Check:** `tjs sum --details` says the rows are rounded to the cent and names the gains files' unrounded total, the figure the estimate showed.
- **Cause:** the estimate summed the unrounded engine gains of the account tables; the RETURN row adds each disposition's cents, as filed.
- **Fix:** nothing to file differently: file the RETURN row. The estimate now taxes the RETURN row's gain (Canada; the US estimate takes its short- and long-term figures from the account books).
- **Fixed in:** `v0.28.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_summary`, `_tax_estimate_result`

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

### `tjs checklist`: "! 4 inputs-frozen: latest activity 2025-12-31 — not through 2026-01-31 yet — tjs fetch" (earlier releases: "— January 2026 is not in the books yet", also over a January trade)
- **Check:** the step's detail names the latest activity date in the books and the date the exports must reach, January 31 of the next year (IB statements are checked per account). A January trade before the 31st does not settle it.
- **Cause:** a December sale settles in January, and a superficial-loss window runs 30 days past it; both need January of the next year in the exports.
- **Fix:** re-export each account through the end of January of the next year (keep the overlapping files: duplicates are dropped row by row), then `tjs run`. If the exports do reach it and nothing happened after the date shown: `tjs checklist --done inputs-frozen --force`.
- **Fixed in:** —
- **Code:** `src/taxjson/lib/checklist.py` — `d_inputs_frozen`, `not through`

### `tjs checklist` in a filed year: "inputs-frozen  filed with the exports as they were at close-year (latest activity 2024-12-16 — …); an export added now is an amendment"
- **Check:** `filed/2024.json` is there (`tjs years` says `filed`).
- **Cause:** once `close-year` froze the year's exports into its own `inputs/<account>/`, a file added there changes the books as filed (its run warns `filed 2024 DRIFTED`). The step used to stay the next one, telling you to download the exports into that folder; it is now done for a filed year, its detail naming the gap the year was filed with.
- **Fix:** nothing, for a year filed as it is. For an amendment: add the export to the year's `inputs/<account>/`, `tjs run` there, and `tjs close-year --force` once the amended return is filed. A later export belongs to the next year's books.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/checklist.py` — `d_inputs_frozen`, `_filed_after_year_end`, `_inputs_reach`

### `tjs checklist` step next-year in a single-folder project: `tjs new-year 2025` says "… is a single-folder project, not a year folder", or `tjs align --from 2024` "no project in …/2024"
- **Check:** `tjs years` in the project says "This is a single-folder project, not a year folder".
- **Cause:** the step listed `new-year`, an `init --single` folder beside the project and `align --from 2024`, with a `prior_year_record` path that assumed the folder is named for its year; `new-year` needs a year folder, and `align` a project in `../2024`. The step now says, in a single-folder project, to run `tjs migrate --to-years` first.
- **Fix:** `tjs migrate --to-years --dry-run`, then `tjs migrate --to-years`: the project becomes the year folder `2024/`, its exports shared by every year. Then `tjs new-year 2025`.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/checklist.py` — `s_next_year`, `migrate --to-years`

### `tjs check-dates`: "1 date(s) cannot be right; fix the source file or the parser."
- **Check:** `tjs check-dates` lists each ERROR row (a settlement on a weekend, before the trade, on a closed market day).
- **Cause:** an impossible date moves a sale to the wrong day's rate or the wrong year; usually a hand-typed `.tt` line or an unusual export row.
- **Fix:** correct the date in the `.tt` file; for a broker export row, report it with `tjs redact` output so the parser can be fixed.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_check_dates`; `src/taxjson/lib/check_dates.py`

### `tjs check-dates`: WARN `settle-after-expiry` on every assignment ("an option expiry (price 0) settles after the contract's expiry date", ACTION `ASSIGN`, a Friday expiry settling the Monday)
- **Check:** the rows are `ASSIGN` legs of an exercise or assignment (price 0, the option's expiry day, settling one cycle later); `tjs check-dates --all` lists them under WARNINGS.
- **Cause:** the check took every $0 option row settling after its expiry for an expiry row. An assignment's option leg is not one: its premium folds into the stock leg, which settles one cycle after the exercise day, and the pair must share a settle date (tax-logic CA-DATE-08: an exercise or assignment takes its stock leg's date), so the parsers keep its T+1 on purpose.
- **Fix:** upgrade; the rows are no longer flagged. The dates were right all along: nothing in the books changes. A real expiry row (an `EXPIRED` or $0 close that is not an assignment) settling after its expiry is still a WARN.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/check_dates.py` — `settle-after-expiry`, `action != "ASSIGN"`

### `tjs reconcile-slips`: `MISMATCH` or `MISSING_FROM_SLIP` against the T5008 / 1099-B
- **Check:** `tjs reconcile-slips inputs/slips/t5008.csv` lists each symbol with the proceeds and cost difference.
- **Cause:** often legitimate: a broker's book value is not the blended ACB, a corporate action has no slip row, an option written this year and still open is reported without a slip (`NO_SLIP_EXPECTED`). Otherwise a missing export or a symbol mismatch.
- **Fix:** explain each difference (the CRA matches Schedule 3 proceeds to the slips); fix real gaps in the inputs. IB's per-type-code T5008 ("Various") cannot be compared: transcribe a per-security CSV.
- **Fixed in:** —
- **Code:** `src/taxjson/bin/taxjson_reconcile_slips.py`; `src/taxjson/bin/taxjson_run.py` — `cmd_reconcile_slips`

### `tjs reconcile-slips`: `MISMATCH` "quantity off by -5.0000 (slip 5.0000 vs computed 10.0000)" for a symbol whose slip rows do not all state a quantity
- **Check:** in the slip CSV, the symbol's rows: one or more have an empty quantity (box 16) cell while the proceeds agree.
- **Cause:** a blank quantity added 0 to the symbol's share count, which was then compared as the slip's total: two 50-proceeds rows, one of 5 shares and one blank, claimed a 5-share discrepancy against a 10-share disposition (2026-10-24 audit B47).
- **Fix:** upgrade: the quantity is then not compared, said in the row's detail ("quantity not compared: 1 of 2 slip row(s) state no quantity"); only shares the slip does state above the computed count are a mismatch. Fill the blank cells from the slip to compare quantities.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_reconcile_slips.py` — `load_slip`, `_compare`, `qty_rows`

### `tjs slip-audit`: "Capital-gains dividends  T5 18 … differs" (or `tjs checklist` step `t5-t3`: "margin: 1 finding(s), e.g. margin (U5***) CAD: capital-gains dividends (T5 18) slip 20.00, books 0.00")
- **Check:** `tjs slip-audit` shows the line, and under Suggestions a `[[capital_gains_dividends]]` table naming the payment (from IB's dividends report, or a `[[slip.line]]` with box 18 in `inputs/slips/slips.toml`).
- **Cause:** a split-share or mutual-fund corporation paid part of a dividend as a capital-gains dividend (T5 box 18, line 17400). No export says so, so the books carry it as a dividend until taxjson.toml names it (tax-logic `CA-INC-06`).
- **Fix:** add the suggested table to taxjson.toml and re-run `tjs run`; `tjs divs-sum` then shows it apart. A payment in lieu's box-18 part cannot be named (the table covers dividends only): it stays in the difference, said in the NOTES (`tjs slip-audit --details`).
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

### `tjs slip-audit`: "margin: Canadian dividends … is on no slip" (`--details`: "… from rbc.csv is on no slip")
- **Check:** the COVERAGE section names the account; `tjs slip-audit --details` also names the input files the income came from.
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

### `tjs slip-audit`: "ZZT.TO: the books already hold ADJUST 3.45 CAD paid 2025-09-30, record date 2025-09-24 (roc.tt), not counted against this slip"
- **Check:** `tjs roc-sum` lists the line; the fund is held at two brokers in one account (or the line's date puts it in another year).
- **Cause:** a hand-entered row has no broker account; it goes with the fund's distribution on its record date or pay date, else with the one slip showing its amount. When that cannot decide, the row is counted against no slip — and slip-audit never suggests a line the books already hold (before, it suggested it again: applying it booked the return of capital twice). The note names the row's pay date and its record date (when it has one), so a row of a prior year's distribution (a December record date) shows as one; slip-audit still never suggests a line the books may already hold (2026-10 audit #97, unreleased).
- **Fix:** give the line its record date (`record=`) or type each broker's T3 with its `broker_account`, so the row is placed; do not add the line again.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/slip_audit.py` — `_audit_account`, `_roc_match`, `_suggest_roc`

### `tjs slip-audit --import-cra`: "Error: pdftotext is not installed"
- **Check:** `pdftotext -v` fails.
- **Cause:** the CRA PDFs are read with pdftotext (poppler-utils).
- **Fix:** `sudo apt install poppler-utils` (Debian, Ubuntu), `brew install poppler` (macOS); or type the slips into `inputs/slips/slips.toml`.
- **Fixed in:** `v0.24.2`
- **Code:** `src/taxjson/lib/cra_slips.py` — `pdf_text`

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

### `tjs slip-audit --import-cra`: a second amended slip is added beside the first and both are counted
- **Check:** `inputs/slips/slips.toml` holds two active `status = "amended"` tables for one issuer, account and fund, and `tjs slip-audit` shows the slip box at about twice the books.
- **Cause:** an amended slip could replace only an original, so an amendment of an amendment was appended; the audit sums every active table (2026-10 audit #109).
- **Fix:** upgrade: the later amended slip replaces the earlier one, which is commented out; where several could be the one it amends, or two amendments of one slip come in one import, none is imported and a note says which. On an older release, comment out the earlier amended `[[slip]]` table by hand.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/cra_slips.py` — `plan_import`

### `tjs slip-audit --import-cra`: "already in slips.toml ([[slip]] #N, the same boxes)" for another issuer's slip, or an older original is added after an amended slip with the same figures
- **Check:** the skipped slip's issuer differs from table #N's (`issuer =` in slips.toml), or slips.toml holds a table with `status = "original"` and the amended figures, and a later import added the older original beside it.
- **Cause:** the duplicate test compared type, account, fund, broker account, currency and boxes, not the issuer, so another issuer's slip with equal boxes was dropped (2026-10 audit #108); and an amended slip whose figures equalled an original there was "the same slip", so its status never became amended and the older original could come back in (#110).
- **Fix:** upgrade: an imported slip's issuer is compared too (a hand-typed slip, with no issuer known, still counts as the same), and an amended slip with an original's figures replaces it. On an older release, type the second issuer's slip into slips.toml by hand, or set `status = "amended"` on the table.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/cra_slips.py` — `plan_import`, `same_issuer`

### `tjs slip-audit`: "the same IB dividends report as … — read once; delete one of them" for two downloads whose base-currency amounts differ
- **Check:** compare the `GrossInBase` / `WithholdInBase` columns of the two CSVs in `inputs/slips/`: they differ while `Gross` / `Withhold` match.
- **Cause:** the report identity compared each component's amounts in the payment currency only, so a corrected base-currency download counted as a copy and whichever file sorted first was kept; the CAD slip figures come from the base amounts (2026-10 audit #89).
- **Fix:** upgrade: such downloads are refused with "two different IB dividends reports … keep the newer download only". On an older release, delete the older download.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/slip_audit.py` — `report_identity`, `audit`

### `tjs slip-audit`: an IB dividends report's T5 (or a fund's T3) total is the sum of two years' payments ("Foreign income  T5 15 … differs" by exactly a payment of the other year)
- **Check:** `tjs slip-audit --json`: the report's `payments` list has pay dates of two years, and the slip's figure is their sum.
- **Cause:** a report holding another year's payments too (a download spanning two years, last year's file left in this year's `inputs/slips/`) passed the year check when the year was among its years, and every payment was totalled (2026-10-24 audit B31).
- **Fix:** upgrade: only the year's payments are on the year's slip — a T3 component (a trust's distribution) by its ex-date's year (the record date's, CA-INC-DATE-TRUST: IB's report for a year lists a December distribution paid in January), every other by its pay date; the per-payment match still names the other year's payments ("paid in 2025; IB's 2026 report includes it"). On an older release, keep one year's report in `inputs/slips/`.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/slip_audit.py` — `ib_slips`, `slip_year`

### `tjs slip-audit`: "slips.toml [[slip]] #N: broker_key … is no broker account in the books — written with another salt" after two `slip-audit --import-cra` runs at once (two shells, a script)
- **Check:** `work/.slip_key_salt` holds one salt, and the tables the second import wrote carry keys it does not resolve; the first import's tables (or the second's) resolve.
- **Cause:** the first import of a project makes the salt; two first imports running at once each made and returned a salt of their own, the last write won, and the keys written with the other salt could never be resolved (2026-10-24 audit A43).
- **Fix:** upgrade: the salt is made under the lock `work/.slip_key_salt.lock` and read back from the file, so both runs return the one salt it holds. For tables already written with a lost salt: delete them and import the slips again.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/slip_audit.py` — `key_salt`, `_salt_in`, `KEY_SALT_FILE`; `src/taxjson/lib/safe_write.py` — `file_lock`

### `tjs slip-audit` suggests `ADJUST … type=roc` for a payment's whole return of capital when the books already hold part of it
- **Check:** `tjs slip-audit --details` shows the security's ROC with books below the slip, and the suggested ADJUST equals the full slip figure, not the gap; `tjs roc-sum` shows an ADJUST type=roc already on that payment.
- **Cause:** for IB's dividends report the suggestion took each payment's whole ROC component and never subtracted the ROC already booked for it, so adding the line lowered the ACB twice (2026-10 audit #96).
- **Fix:** upgrade: only the rest is suggested, and a note names the existing line to change to the full amount instead; a booked ROC row near none of the report's payments is named too. On an older release, add only the difference, or change the existing ADJUST to the full amount.
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/lib/slip_audit.py` — `_suggest_roc`

### `tjs slip-audit`: "Return of capital T3 42 … differs" with the books' figure below the slip (or negative), and a suggested `ADJUST … type=roc` for the gap, beside a reinvested or notional distribution
- **Check:** `tjs roc-sum` of the year: the security has a `[[distributions]]` entry with a positive `per_share` (a reinvested distribution) or an RBC "NOTIONAL DISTRIBUTION ADJUSTMENT TO BOOK COST" row (or a `.tt` ADJUST `type=dist`), and the books' return of capital in the audit is the real one less that raise.
- **Cause:** slip-audit counted every ADJUST row of the year as T3 box 42, so an ACB raise offset the return of capital: a correct slip showed as "differs" (exit 1), and the suggested line would cut the ACB by the reinvested amount and overstate the later gain (2026-10-23 audit #5).
- **Fix:** upgrade: only the ADJUST rows that are a return of capital count (a broker's ROC, a `.tt` ADJUST with no type or `type=roc`, a `[[distributions]]` entry with a negative factor); `tjs roc-sum` lists the raises apart under OTHER ACB ADJUSTMENTS. On an older release, do not add the suggested line; compare box 42 with the ROC rows of `tjs roc` by hand. A reinvested distribution's income (box 21) is still not in the books (`CA-DIST-01`): the box-21 difference stays, and the slip is what you file.
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/lib/income_dating.py` — `is_return_of_capital`; `src/taxjson/lib/slip_audit.py` — `load_books`

### `tjs roc-sum`: "TOTAL CAPITAL RETURNED (ACB reduced)" counts an s.86.1 spin-off, and `tjs sanity` gives `return-of-capital` as the parent's cost reason
- **Check:** the security had a spin-off elected `rollover_s_86_1` (`tjs elect` lists it), there was no return of capital, and roc-sum's MANUAL_ROWS counts one row on the parent with the allocated cost.
- **Cause:** the election books the parent's cost reduction as an ADJUST with no type; roc-sum counted every such row as capital returned (beside the hint to compare with T3 box 42), and sanity labelled any lowered cost a return of capital (2026-10-23 audit #11). The books' ACB was right.
- **Fix:** upgrade: roc-sum lists the spin-off's cost allocation apart under OTHER ACB ADJUSTMENTS (`--json`: `other_adjustments`), outside the total, and sanity's reason is `spin-off`. On an older release, leave the spin-off row out of the box-42 comparison by hand.
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_roc_sum`, `_roc_sum_other`, `_adjust_kind`; `src/taxjson/lib/positions_check.py` — `roc_symbols`, `spinoff_symbols`

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
- **Cause:** a Canadian slip is in CAD, so a USD-base account's report is converted payment by payment at the Bank of Canada rate of its pay date (tax-logic `CA-SLIP-02`; `tjs slip-audit --details` gives the year's average-rate figure too). Before, it was compared in USD as if it were CAD.
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

### `tjs close-year`: "Error: 3 checklist item(s) before the lock need attention" followed by "- inputs-committed: not a git repository" and the like
- **Check:** `tjs checklist` lists the same steps as `! ` lines (`--all`: `[!]`).
- **Cause:** the lock records the filed year; a step before it that needs attention (inputs not committed, missing history, an undecided send …) usually means the books are not final. Earlier releases locked the year without a word.
- **Fix:** fix the items (the checklist says how), mark one `--done` (`--done ID --force` over a finding) when it truly does not apply, then close. To lock anyway: on a terminal answer `y`; in a script pass `--yes`.
- **Fixed in:** `v0.28.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_close_year_attention`, `cmd_close_year`, `checklist item(s) before the lock need`

### `tjs close-year` on a terminal asks "Lock 2025 anyway? [y/N]" under "Warning: 2 checklist item(s) before the lock need attention" without naming them
- **Check:** `tjs close-year --details` lists them above the question; piped, the refusal lists them.
- **Cause:** the warning followed the essentials-first rule (its items behind `--details`), though the question is about those items.
- **Fix:** upgrade: the items are listed above the question in the default view.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_close_year`, `Lock {year} anyway?`

### `tjs close-year` locked the year with inputs not committed in a git repository: no question, no refusal, though `tjs checklist` names "inputs-committed: commit inputs/, taxjson.toml and ticker.map"
- **Check:** `git status` shows changes under `inputs/` or the config; `tjs checklist` shows step inputs-committed as the next step (`[>]`, `--json` status `todo`).
- **Cause:** the gate kept only steps that need attention; inputs-committed is "to do" for a dirty tree (only a project with no git repository is "attention").
- **Fix:** upgrade: uncommitted inputs stop the close (piped) or ask (terminal), naming `- inputs-committed: N uncommitted change(s) under inputs/config`. Commit, then close; `--yes` locks anyway.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_close_year_attention`, `_CLOSE_YEAR_TODO`; `src/taxjson/lib/checklist.py` — `d_inputs_committed`

### `tjs close-year`: "- handoff: — `taxjson handoff`" with no reason
- **Check:** `tjs handoff` lists the problem under CARRY-FORWARD INPUTS VS THE 2024 RECORD (an `[estimate] other_losses`, `long_term_losses`, `amt_carryover` or `[carryover] claimed` entry that differs from what the record carried out).
- **Cause:** the checklist's handoff step named every kind of handoff problem but the carry-forward one.
- **Fix:** upgrade: the line says "1 carry-forward input(s) differ from the 2024 record". Fix whichever is wrong (your notice of assessment decides).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/checklist.py` — `d_handoff`, `carry-forward input(s) differ`

### `tjs estimate` / `tjs amt` / `tjs carryover`: "error: filed/2025.json: carryforwards.net_capital_loss has no closing — the balance carried out of the year is missing (a truncated block) — restore the lock from git or re-close that year"
- **Check:** open the close-year record named: its `carryforwards` block has the section (`net_capital_loss`, `capital_loss`, `minimum_tax`) but not the key named (`closing`; `st_closing` / `lt_closing`; `closing_by_year`). The same lock cut in half says "not valid JSON", and a balance written `NaN` says "carryforwards.net_capital_loss.closing is not a number". In the closed year's project, `tjs check-filed` says "filed 2025 could not be checked" and `tjs years` shows the year "lock damaged".
- **Cause:** a hand-edited or truncated block passed the shape check without its closing balance, and the next year's estimate read the missing balance as 0.00: a loss carry-forward silently reset (2026-10-24 audit B39). close-year always writes the balance; only a damaged record lacks it. `tjs carryover` still dropped such a lock (or one cut in half, or with a `NaN` balance) with a Warning and printed the carryforward without it, 0.00 when the books do not rebuild that year, at exit 0 (2026-10-25 audit robustness#1): it now stops with the estimate's error.
- **Fix:** restore the record from git, or re-close that year (`tjs close-year --force` in its project, only if that year's figures are right); the estimate and the carryover ledger refuse until then. Or enter the balance yourself (`[estimate] other_losses`, `amt_carryover`): input you give always wins in the estimate.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/carryforward.py` — `block_problem`, `_BLOCK_CLOSING`, `lock_block`; `src/taxjson/bin/taxjson_filed.py` — `read_lock`, `lock_damage`; `src/taxjson/bin/taxjson_carryover.py` — `lock_figure`, `LockUnreadable`

### `tjs carryover` / `tjs estimate` / `tjs amt`: "Error: ../2024/filed/2024.json was closed under country = "usa" (United States law) but this project is country = "canada" (Canada): the other country's carry-forwards, positions and cost cannot open a Canada year, so the lock is not read"
- **Check:** `[settings] prior_year_record` in taxjson.toml names a lock whose `"country"` is not this project's (the other country's project, or a copy of it). `tjs handoff` refuses the same lock.
- **Cause:** a lock closed under the other country's law carries none of this country's carry-forwards (a US lock has no net capital loss or minimum tax; a Canadian one no short/long-term carryover). `tjs carryover` took it as the filed figure 0.00, and the estimate and `tjs amt` read it as carrying nothing ("no earlier close-year record"), at exit 0: the carried loss vanished from the balance and the tax (2026-10-25 audit year-end#1). `handoff`, `check-filed` and `fx-cash` already refused it; every lock reader now does.
- **Fix:** point `prior_year_record` at the previous year's lock of this country's project. If this project's `[settings] country` is the wrong one, fix it instead.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_filed.py` — `read_lock`, `lock_country_problem`; `src/taxjson/lib/carryforward.py` — `lock_block`, `_from_lock`; `src/taxjson/bin/taxjson_carryover.py` — `lock_figure`; `src/taxjson/bin/taxjson_run.py` — `cmd_amt`

### `tjs amt` / `tjs estimate` / `tjs handoff` / `tjs check-filed`: "carryforwards.minimum_tax.closing_by_year gives 2024 twice ('2024' and '02024') — one entry per year of origin", "... 2024 is -900.0, a negative amount" or "carryforwards.minimum_tax.closing is 900.00 but its closing_by_year adds up to 500.00"
- **Check:** open the lock named: in its `carryforwards` block a by-year table (`closing_by_year`, `opening_by_year`, `recovered_by_year`, `expired_by_year`) gives one year two ways or a negative amount, a balance is negative, or the minimum tax `closing` is not the sum of `closing_by_year`; or the file writes one key twice in one object ("the key '2024' is written twice"). close-year writes one entry per year, amounts of 0 or more, and the closing as their sum: only a hand edit makes these.
- **Cause:** the next year read `02024` as 2024 and kept the second amount, dropped a negative amount, and took an edited by-year amount over the closing, with no word: `tjs amt` recovered 50.00 of a 900.00 carryover, and `check-filed` and `handoff` said OK (2026-10-25 audit year-end#3). `[estimate] amt_carryover` was already refused the same way (2026-10-24 audit A39).
- **Fix:** restore the lock from git, or re-close that year (`tjs close-year --force` in its project, only if its figures are right). Or enter the carryover from your notice of assessment / T691 in `[estimate] amt_carryover`: input you give wins in the estimate.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/carryforward.py` — `block_problem`, `_by_year_problem`, `_BLOCK_BALANCES`; `src/taxjson/bin/taxjson_filed.py` — `load_lock_json`, `_unique_keys`

### `tjs check-filed` / `tjs run`: "Warning: filed 2024 could not be checked: filed/2024.json: carryforwards.net_capital_loss has no closing …" or "… filed/2024.json says year 2023, not 2024 …"; an older release said "filed 2024: OK" for that lock
- **Check:** the next year's `tjs estimate`, `tjs carryover` and `tjs handoff` refuse the same lock with the same words; `tjs years` shows the year "lock damaged".
- **Cause:** the filed year's own checks (`check-filed`, the run's filed-year check and `run --strict`) looked only at the locked totals and called a lock OK that the next year refused as damaged: a carry-forward block without its closing balance or with a `NaN` one, or a `year` field naming another year — which the next year read as that other year's record (the hand-off checked "2023 into 2024", and the carryover ledger counted the year's loss twice) (2026-10-25 audit year-end#6). Every lock reader now applies one check (`read_lock`).
- **Fix:** restore the lock from git, or re-close the year in its project (`tjs close-year --force`, only if its figures are the filed return's).
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_filed.py` — `read_lock`, `lock_damage`, `file_year`; `src/taxjson/bin/taxjson_run.py` — `_check_filed_years`, `filed {year} could not be checked`; `src/taxjson/lib/project_layout.py` — `years_report`; `src/taxjson/lib/handoff.py` — `validate_record`

### `tjs years` / `tjs check-filed`: "filed/2025.json records accounts.margin.realized as nan, not a finite number — the lock is damaged"; or an older release's `check-filed` said OK for a year whose lock held `NaN` totals
- **Check:** open the record: a figure written as `NaN`, `Infinity`, `null`, `true` or a quoted number in `accounts.<label>`, `totals` or `form_lines` (a hand edit or a damaged copy; close-year writes plain numbers).
- **Cause:** every comparison with a NaN is false, so a lock whose realized total was `NaN` passed the shape check and the drift check found no difference from any recomputed figure (2026-10-24 audit B38).
- **Fix:** restore the record from git, or re-close the year (`tjs close-year --force`, only if that year's return was filed on the recomputed figures); until then `tjs years` lists the lock as a problem and `check-filed` counts it as unreadable.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_filed.py` — `lock_shape_problem`, `diff_snapshot`, `_finite_num`, `_NUMERIC_KEYS`

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

### `tjs checklist`: "! 20 inputs-committed (blocked): not checked: this repository's own git config sets... — git status"
- **Check:** `tjs checklist --all` shows the whole reason under "[b] 20. inputs-committed": "not checked: this repository's own git config sets filter.crypt.clean, a command git would run …"; when `lock-committed` is blocked for the same reason the two share one line ("! 2 blocked (20 inputs-committed, 45 lock-committed): not checked: …"). `git config --show-scope --get-regexp '^(filter|diff)\.|^core\.attributesfile'` in the project lists the key with scope `local`. The same reason shows on `lock-committed`.
- **Cause:** `git status` runs the clean / process filter (and a diff textconv) that the repository's own `.git/config` names for the files `.gitattributes` assigns it to. A project folder received from someone else could run any command that way, so the checklist runs no git command in a repository whose own config sets one; filters in your global or system config (git-lfs) are fine.
- **Fix:** if you set that filter yourself and trust it, check `git status` by hand and mark the step: `tjs checklist --done inputs-committed`. Otherwise remove it (`git config --unset filter.crypt.clean`, and the `.gitattributes` line) and run the checklist again.
- **Fixed in:** `v0.25.0`
- **Code:** `src/taxjson/lib/checklist.py` — `_git_refusal`, `_GIT_COMMAND_KEYS`, `_git_status`, `d_inputs_committed`, `d_lock_committed`

### `tjs checklist`: "! 45 lock-committed: filed/2024/reports/ is git-ignored (.gitignore's `reports/`), so it is not committed …"
- **Check:** `git status --short --ignored filed` shows `!! filed/2024/`; `git check-ignore -v filed/2024/reports/*` names the rule. On an older release the step said `committed` while `git ls-files filed` listed the lock alone.
- **Cause:** the `.gitignore` an older `tjs init` wrote has an unanchored `reports/` line, which ignores the reports as filed (`filed/<year>/reports/`) too; `git status` never lists an ignored file. `close-year` prints the same note.
- **Fix:** anchor the lines (`/reports/`, `/work/`, `/export/`; in a folder of year folders also `/*/reports/` …, as a new `tjs init` writes them), then `git add filed/` and commit.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/checklist.py` — `d_lock_committed`, `_filed_ignored`

### `tjs checklist`: "! 45 lock-committed: not a git repository — git init"
- **Check:** `git status` in the project says "not a git repository"; in a folder of year folders, run it in the folder holding them.
- **Cause:** the lock is committed to a git repository the project does not have yet. The step used to name only `git status filed/`, which fails there.
- **Fix:** `git init` in the project (`git -C .. init` from a year folder: one repository for the year folders and the shared `inputs/`), then `git add` the project's files (`.gitignore` keeps out `work/` and `reports/`) and commit, `filed/<year>.json` and `filed/<year>/reports/` with them.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/checklist.py` — `d_lock_committed`, `git -C .. init`

### "Warning: filed 2024 DRIFTED vs 2024.json" after an upgrade, for a sale with a trade of the same security in another account at the same moment
- **Check:** `tjs check-filed` lists the figure that moved; `tjs wash-sales --explain` shows the sale and, at the same date and time, a purchase or sale of the same security in another account (the generic importer and Webull print no clock time, so their rows of one day share one).
- **Cause:** taxjson v0.28.1 and older took rows of two accounts at one moment in the order the accounts are listed in taxjson.toml, and reordering the file changed the gains without calling the books stale (issue #31). They now go in the accounts' name order, whatever the file's order (`CA-DATE-14` / `US-DATE-13` in `tjs tax-logic --ids`).
- **Fix:** if you know which trade came first, give the rows their clock times (a `.tt` line in place of the broker row). Otherwise review the moved figure: amend the return, or refresh the lock with `tjs close-year --force` if the new figure is the one you filed.
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/lib/corporate_timeline.py` — `account_tie`, `event_sort_key`; `src/taxjson/lib/core.py` — `_by_account_name`

### US project: "inputs changed since the last full run (added: filed/ (locked years))" after `tjs close-year`, or after adding or removing a `filed/<year>.json`
- **Check:** `tjs sum --details` or `tjs checklist` names `filed/ (locked years)` (`changed:` when the project already had a lock, `removed:` when its last lock was deleted). `ls filed/` (and `[settings] prior_year_record`) shows the locks.
- **Cause:** a US gains run books a wash-sale basis add that reaches a sale in a filed year in the loss's year instead (`US-WASH-22`), so the set of locked years is an input of the books. On v0.28.1 and older a new lock left the books looking current although a run would change them (issue #32). A Canadian project's gains read no lock.
- **Fix:** `tjs run`.
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/lib/checklist.py` — `LOCKED_YEARS_KEY`, `_locked_years_entry`, `input_fingerprint`; `src/taxjson/bin/taxjson_filed.py` — `locked_year_flags`

### US project: "Info: filed 2024: 1 frozen input(s) changed since the year was closed: filed/ (locked years)" on every run and `check-filed` after `tjs close-year`; `tjs compare filed`: "Inputs: 1 added: filed/ (locked years)"
- **Check:** the only name listed is `filed/ (locked years)`; `tjs run` again does not clear it, and `tjs check-filed` still ends `filed 2024: OK`.
- **Cause:** close-year records the frozen inputs before it writes the year's own lock, and a US project's input fingerprint also holds the set of locked years (`US-WASH-22`), so every later comparison found that entry "added". It is not an export to restore.
- **Fix:** upgrade: the frozen comparison now holds the files only, for a lock written before as well (no re-close needed).
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/lib/checklist.py` — `frozen_files`, `fingerprint_changes`, `LOCKED_YEARS_KEY`; `src/taxjson/lib/compare.py` — `frozen_files`; `src/taxjson/bin/taxjson_run.py` — `_freeze_inputs`, `_frozen_inputs_changed`

### "Info: filed 2024: 1 frozen input(s) changed since the year was closed: inputs/crypto/crypto_sends.tt — … (restore them from your records …)" on every run and `check-filed` of a filed year with a crypto send decided; `tjs compare filed`: "Inputs: 1 added: inputs/crypto/crypto_sends.tt"
- **Check:** the only file named is `crypto_sends.tt`; it starts "# GENERATED by `taxjson crypto-sends --write`"; the year reads a shared `inputs_dir` (now commented out by the freeze).
- **Cause:** with shared exports the year generates its crypto_sends.tt in `work/crypto_sends/<account>/`, so the freeze did not record it; once frozen, the year writes it in its own `inputs/<account>/`, a file the lock did not have. It follows from the frozen `sends.json` and exports: nothing to restore.
- **Fix:** upgrade: close-year copies it into `inputs/<account>/` with the exports, and a lock written before is compared without it (no re-close needed).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_freeze_inputs`, `_frozen_inputs_changed`; `src/taxjson/lib/checklist.py` — `unrecorded_generated`; `src/taxjson/lib/compare.py` — `unrecorded_generated`

### After `tjs close-year`, `tjs check-dates` in the filed year: "ERRORS — impossible dates (6)" / "out-of-range: 6 — far outside the project year 2023" for rows of 2025 and later (exit 1); the checklist's check-dates step and the re-close gate say the same
- **Check:** `grep -n "## frozen by" 2023/taxjson.toml` (the year was frozen from the shared exports); before the close the same rows were counted, not errors.
- **Cause:** later years' rows are expected in exports shared by every year, and the freeze copies those files whole into the year's `inputs/`; once `inputs_dir` was commented out, check-dates judged the copy as a single-folder project's.
- **Fix:** upgrade: a year whose lock records the shared folder it was frozen from counts them, as before the freeze (`--details`: one Info line). Do not edit the frozen copy.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/check_dates.py` — `analyze`, `later_frozen`; `src/taxjson/lib/project_layout.py` — `frozen_from_shared`

### `tjs close-year --force` locked books whose last run did not finish: `filed/<year>/reports/` is empty and `tjs compare filed` lists every report as added
- **Check:** `ls reports/*.sum` finds none; `tjs close-year` says "the last `taxjson run` did not finish: reports/ has no .sum, but work/ holds the partial books it wrote".
- **Cause:** that refusal offered `--force`, which locked the partial books, though commands.md said such books are refused.
- **Fix:** upgrade: refused even with `--force`. Fix the run and re-run `tjs run`, then close. A year already locked this way: re-run `tjs run` and close again with `--force` once the books are the filed ones.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_close_year`, `_RUN_UNFINISHED`, `_run_state_problems`

### `tjs close-year --force` with an account deferred at a pending election: "Error: wash-adjusted gains for … are STALE" / "A --account rerun skipped the cross-account wash pass", though no `--account` run happened
- **Check:** `tjs elect --pending` lists the election; `tjs run` exits 3 with "account deferred".
- **Cause:** a run deferred at an election skips the cross-account pass, as `run --account` does; the refusal assumed the latter, and the first refusal offered a `--force` that could not lock these books.
- **Fix:** upgrade: the refusal names the deferred account and `taxjson elect --pending`, and `--force` is offered only where it would lock the books. Make the election (`tjs elect ACCOUNT --set …`), `tjs run`, then close.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_close_year`, `_why_unblended`, `deferred`

### `tjs check-filed` in the folder holding the year folders says to run it in the newest year, which answers "No filed/<year>.json snapshots — `taxjson close-year` creates one after you file." though earlier years are filed
- **Check:** `tjs years` shows the filed years; each has its lock in its own folder (`2024/filed/2024.json`).
- **Cause:** a lock lives in its year's folder and check-filed checks the project's own `filed/`; the hint named the newest year folder, usually the open one.
- **Fix:** upgrade: both answers name the filed year folders (`tjs -C 2024 check-filed`); run it there.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_refuse_years_root`, `_filed_year_folders`, `cmd_check_filed`

### `tjs close-year` and `tjs years` show a realized (or disallowed) total a cent or two off the RETURN row of `tjs sum`
- **Check:** `tjs sum --details` says the rows are rounded to the cent and names the gains files' unrounded total, the figure close-year showed.
- **Cause:** the lock's totals round the engine's unrounded sum once (what `tjs check-filed` compares); the RETURN row adds each sale's cents, as filed. close-year and years showed the lock's totals.
- **Fix:** nothing to file differently: file the RETURN row. After upgrading, close-year records the RETURN row's figures in the lock (`return_row`) and it and `tjs years` show them (`--details` names the engine totals beside them); a year closed before shows its totals until it is closed again.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_return_row_totals`, `cmd_close_year`, `return_row`; `src/taxjson/lib/project_layout.py` — `years_report`

### `tjs close-year`'s "Income:" (and `tjs years --json`'s `totals.income`) is a cent off `tjs sum`'s DIVIDEND total
- **Check:** the project has two taxable accounts or more; `tjs close-year --details` names the lock's income total beside the one it shows ("the lock's total the drift check compares is …").
- **Cause:** the lock's income total rounds the accounts' unrounded sum once (what `tjs check-filed` compares); `tjs sum`'s taxable SUBTOTAL adds each account's DIVIDEND and PIL to the cent. close-year and years showed the lock's total.
- **Fix:** nothing to file differently. After upgrading, close-year and `tjs years --json` show income as `tjs sum`'s SUBTOTAL adds it (DIVIDEND plus PIL, each account's cents), for a year closed before too; the lock's totals are unchanged.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_filed.py` — `income_as_summed`; `src/taxjson/bin/taxjson_run.py` — `cmd_close_year`; `src/taxjson/lib/project_layout.py` — `years_report`

### `tjs sum`: the taxable SUBTOTAL's REALIZED (or the TOTAL of a one-table project) is a cent or two off the RETURN row's GAIN
- **Check:** the FOR THE RETURN legend says "The SUBTOTAL adds the account rows, RETURN each sale's cents"; `tjs sum --details` names the gains files' unrounded total too.
- **Cause:** SUBTOTAL adds the account rows, each account's gain rounded once; the RETURN row adds each Schedule 3 (Form 8949) row's cents, as filed. Before, only `--details` said why they differ.
- **Fix:** nothing to file differently: file the RETURN row. After upgrading, the default view says it in one line above the RETURN table whenever the two differ.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_summary`, `_rounding_legend`

### `tjs years`: "lock damaged" and "! 2024: filed/2024.json is not a close-year lock (an array, not a JSON object)"
- **Check:** open `filed/2024.json`: it is not the object `tjs close-year` writes (an array, a string, an `accounts` that is not a table of accounts, `totals` that are not a table, or a `year` other than the file's). `tjs check-filed` refuses the same file. On v0.28.1 and older `tjs years` showed such a year as filed with no problem (issue #49).
- **Cause:** the lock was edited by hand, truncated, or replaced by another file.
- **Fix:** restore it from git (`git log -- filed/2024.json`), or, after checking the year's figures against the return you filed, write it again with `tjs close-year --force` in that year's folder.
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/bin/taxjson_filed.py` — `lock_shape_problem`; `src/taxjson/lib/project_layout.py` — `years_report`; `src/taxjson/bin/taxjson_run.py` — `_years_table`

### `tjs estimate`: "! Carryovers come from the 2024 lock, taken before 2024 ended: provisional"
- **Check:** `tjs estimate --details` lists the note "The carried balances are provisional: the 2024 lock was taken on 2024-06-01, before the year ended (close-year --force) …"; `tjs years` marks the year `filed*`. On v0.28.1 and older the estimate used the balances without saying so (issue #55).
- **Cause:** the net capital loss or minimum tax carried into this year comes from last year's lock, and that lock was written with `close-year --force` before last year ended: a snapshot, not the return you filed.
- **Fix:** after filing last year, run `tjs close-year --force` in last year's project, or enter the figures from your notice of assessment (`[estimate]` `other_losses`, `amt_carryover`).
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/lib/carryforward.py` — `resolve_losses`, `resolve_amt`, `partial_lock`; `src/taxjson/bin/taxjson_filed.py` — `partial_year_note`; `src/taxjson/bin/taxjson_run.py` — `Carryovers come from the`

### A year locked before it ended (`tjs close-year --force`) never gets its later trades: `tjs run` and `tjs check-filed` say "Filed 2026: OK", `tjs sum` leaves the later sales out, and closing again after filing locks the same partial figures
- **Check:** `tjs years` marks the year `filed*` (locked before the year ended); `grep -n "## frozen by" 2026/taxjson.toml` finds the shared `inputs_dir` commented out, and `2026/inputs/<account>/` holds a copy of the exports as they were on the lock's date. The close said "Frozen: inputs/ (1 file(s) copied from ../inputs …)".
- **Cause:** v0.31.0 and v0.31.1 froze the inputs on every close, a partial-year lock too: the year read the copy from then on, so a trade saved in the shared exports later never reached its books, although the close's warning said "Later trades in 2026 will show as drift". A re-close kept the earlier copy.
- **Fix:** upgrade. A lock taken before the year ended now hashes the inputs and copies nothing ("Frozen: nothing — 2026 has not ended …"): the year keeps reading the shared exports, a later trade makes `tjs check-filed` warn `filed 2026 DRIFTED` ("a snapshot taken before 2026 ended"), and the close after filing freezes the whole year. For a lock an older release froze, `tjs close-year --force` first thaws it ("Thawed: taxjson.toml reads ../inputs … again"; the unchanged copies are removed, the lock becomes a partial snapshot) and stops: `tjs run`, then close the year after filing. A copy edited since the freeze is named and kept ("changed since the freeze"): move it into the shared folder if it belongs to the books, then close again.
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_freeze_inputs`, `_thaw_partial_freeze`, `_thaw_text`, `_frozen_summary`, `_check_filed_years`; `src/taxjson/lib/project_layout.py` — `frozen_year`

### `tjs handoff`: no "in neither return" warning for a Dec 31 or early-January income row
- **Check:** two accounts hold the same security in the same size (identical Dec 31 income rows), or the closed year's inputs held two identical early-January rows and this project holds one.
- **Cause:** boundary matching ignored the account and let one current row match several record rows.
- **Fix:** upgrade and run `tjs handoff` again.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/handoff.py` — `_same_row`, `_boundary_issues`, `acct_of`

### `tjs fx-cash --ledger v2`: two accounts of one broker show as one book, and `at=` "names no single" account
- **Check:** `tjs fx-cash --ledger v2 --json --cash-events`: every event of both broker accounts lands in one book such as `margin/ib:3135`, and an `at=ib:3135` line is refused with "names no single ib account".
- **Cause:** a book was named by the first 4 characters of the hashed broker account; two accounts whose hashes begin alike became one book.
- **Fix:** upgrade: the two books get longer names (the refusal lists them); point each `at=` or `CASHBOOK` line at one of them.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/cash_events.py` — `_short_width`, `book_name`, `Books`

### `tjs fx-cash --ledger v2`: "no opening pool for …" although the .tt file has a CASHOPEN line
- **Check:** the `.tt` file holding the `CASHOPEN` has no `at=` and no `CASHBOOK` line, and its account folder holds several broker books.
- **Cause:** a CASHOPEN whose book could not be told was dropped without a message.
- **Fix:** upgrade: the line is reported by name; add `at=<book>` to it, or a `CASHBOOK <book>` line to the file.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/fx_cash_v2.py` — `build`, `CASHOPEN`

### `tjs fx-cash --ledger v2`: "no USD rate on file" for an FXCONV line that gives value=
- **Check:** the refused conversion is between two foreign currencies and its `.tt` line carries `value=`.
- **Cause:** the ledger looked up the from-currency's rate before using the given value.
- **Fix:** upgrade: `value=` prices the conversion; a rate is needed only when the conversion overdraws the currency.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/fx_cash_v2.py` — `build`, `rate_of`

### `tjs run`: ALLOWLOSS line "is not a number of units sold" for a quantity like `1,5`
- **Check:** the ALLOWLOSS quantity has a decimal comma or a malformed thousands group.
- **Cause:** earlier releases dropped the comma and read `1,5` as 15 units; the quantity is now read strictly.
- **Fix:** write the quantity with a decimal point (`1.5`) or plain digits.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/loss_overrides.py` — `parse_line`, `parse_strict_number`

### `tjs slip-audit`: a USD T5008 shows SLIP PROCEEDS 0.00
- **Check:** `tjs slip-audit --details` says "Annual average USD/CAD: none".
- **Cause:** the FX cache had no Bank of Canada rates for the year, and the slip was left out of the sum, which showed as 0.00.
- **Fix:** upgrade (the cells now show "—" with a note), then run `tjs run` to fill the FX cache.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/slip_audit.py` — `_t5008`, `render`

### `tjs slip-audit`: one weekly dividend is "not in the books" and another "not on IB's report"
- **Check:** both findings are for the same security, a few days apart, and IB's report and the export date the payments 4 or more days apart.
- **Cause:** each report payment took the nearest book payment, which left the next payment with nothing within 7 days.
- **Fix:** upgrade; payments now pair in date order.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/slip_audit.py` — `_match_payments`, `_pair_in_order`

### `tjs slip-audit`: an imported T5 is "ambiguous: 2 … broker accounts match it equally"
- **Check:** one project account holds two broker accounts of the slip's issuer with the same year income.
- **Cause:** nothing in the books tells which broker account the slip belongs to; older releases put it in the first one.
- **Fix:** type the slip into slips.toml with `broker_account` set.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/cra_slips.py` — `place`

### `tjs slip-audit --import-cra`: "skipped t5.pdf: box 15 reads '1,50.00', not an amount — the page cannot be read cleanly; not imported"
- **Check:** open the PDF; the box's figure, or pdftotext's reading of it (`pdftotext -layout t5.pdf -`), is not a d,ddd.dd amount.
- **Cause:** a known amount box whose value wasn't a clean amount was skipped, so the slip was imported without that box. It is now refused (a value with no digit, such as a wrapped label word, is still skipped).
- **Fix:** print the slip page from CRA My Account again, or type the slip into inputs/slips/slips.toml.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/cra_slips.py` — `_one_slip`, `_AMOUNT_RE`

### `tjs slip-audit`: "… IB's report: 100.00 USD; books: 100.00 CAD on … — a currency mismatch"
- **Check:** `tjs slip-audit --details` lists it as "Currency differs: SYMBOL DATE slip … USD, books … CAD on DATE"; `--json` has it under `payments.currency_differs`.
- **Cause:** IB's report pays the dividend in one currency, and the only books row of that security within 7 days is in another (often a hand-typed `.tt` DIVIDEND with the wrong currency, or the other listing of an interlisted security). Earlier, the two were matched as equal without comparing currencies.
- **Fix:** correct the currency of the books' row (the `.tt` line), or add the missing row in the payment's currency, then `tjs run`.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/slip_audit.py` — `_match_payments`, `currency_differs`, `currency-differs`

### `tjs slip-audit` adds a security's CAD and USD slip amounts into one row and suggests one line in the last currency
- **Check:** `tjs slip-audit --json`: `securities` has one row per security and currency.
- **Cause:** two slips (or slip lines) for one security in two currencies were summed per security, in the last slip's currency. Each currency is now its own row, comparison and suggestion.
- **Fix:** upgrade; nothing to change in slips.toml.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/slip_audit.py` — `_securities`, `_suggest_roc`

### `tjs slip-audit`: "has no record= and is dated by its pay date, but the distribution paid … has record date …" names another listing's distribution
- **Check:** the message names a distribution of another listing of the same root (SAMP.TO for a SAMP.US line).
- **Cause:** the ROC line was matched to any distribution of its root within 3 days. It now follows its own listing's (symbol and currency) distribution, and another listing's only when its own paid nothing then (an interlisted fund).
- **Fix:** upgrade. If the line is really the other listing's distribution, add its `record=`.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/slip_audit.py` — `_redate_prior_roc`

### `tjs compare` exits 1 with "COMPARED reports/ with the previous run (…): the 12 report file(s) match, but 1 account figure(s) moved"; on an older release it said "No difference" and exited 0
- **Check:** `tjs compare --json`: `reports.changed` is empty and `accounts` lists the moved figure.
- **Cause:** only the report files decided the result, and the figure table was printed only when a file differed. A figure can move without a report changing when work/<account>_gains.json was edited by hand or rebuilt apart from the reports.
- **Fix:** run `tjs run` so the reports are rebuilt from the same books, then compare again.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/compare.py` — `compare`, `render`

### `tjs compare` (or `--save`): "Error: reports/a.rpt cannot be read: Permission denied"; `tjs run`: "warning: the previous reports were not kept for `taxjson compare`: reports/ cannot be read: …"; on an older release the file was counted as removed or left out of the snapshot
- **Check:** `ls -l reports/ work/compare/prev/reports/` for a file or folder the user cannot read.
- **Cause:** a read error was treated as "no such file".
- **Fix:** restore the permissions (`chmod u+rw` on the file, `u+rwx` on the folder) and run again.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/compare.py` — `report_files`, `take`, `rotate_previous`

### `tjs compare --save baseline` removed the snapshot `baseline.part` (`tjs compare --list` no longer names it); or a full `tjs run` removed `prev.part`
- **Check:** `tjs compare --list` before and after a `--save NAME` (or a full run, which rewrites `prev`): a snapshot named `NAME.part` is gone; `ls work/compare/`.
- **Cause:** the new snapshot was staged under the fixed name `work/compare/NAME.part/`, and that folder was removed first to make room. `NAME.part` is a valid snapshot name, so a snapshot saved under it was the one removed.
- **Fix:** upgrade: a snapshot is staged in a folder of its own whose name starts with a dot (no snapshot is named so), and `--list` ignores such folders. A snapshot an older release removed this way is gone: rebuild those reports and save it again.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/compare.py` — `take`, `_HIDDEN`, `list_snapshots`

### `tjs compare saved`: "Error: no snapshot named 'saved'" right after a `tjs compare --save saved` that failed; or `tjs compare`: "no previous run kept yet" after a `tjs run` that warned "the previous reports were not kept for `taxjson compare`"
- **Check:** `ls work/compare/`: the folder of that name is missing although an earlier save (or full run) had made it, and the failed command's message named a rename or disk error (`No space left on device`, `Permission denied`).
- **Cause:** replacing a snapshot deleted the earlier one before renaming the new one into place; when that rename failed, the name was left empty.
- **Fix:** upgrade: the earlier snapshot is moved aside, the new one renamed in, and when that fails the earlier one is moved back ("… cannot be replaced (…); the earlier snapshot is kept", exit 2). If even the move back fails, the message says where it is (`work/compare/.saved.<random>.old/saved/`): move that folder to `work/compare/saved/`. A snapshot an older release lost cannot be recovered.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/compare.py` — `_publish`, `cannot be replaced`

### `tjs compare saved`: "AttributeError: 'list' object has no attribute 'get'" (older release), or "Error: work/compare/saved/snapshot.json is not a snapshot (accounts is a list, not a table of accounts) — `taxjson compare --delete saved` removes it"
- **Check:** open `work/compare/saved/snapshot.json`: the field the message names does not have the shape taxjson writes (`files` a table of inputs, `accounts` a table keyed by account, each a table of figures, `reports` a list of file names, `kind` and `taken` text). A snapshot taxjson wrote has them; a hand-edited or truncated one may not.
- **Cause:** the loader checked only `files`; a malformed `accounts` or `reports` passed, and the comparison (or `--list`) crashed on it.
- **Fix:** `tjs compare --delete saved`, then save it again when you can (`prev` is rewritten by the next full `tjs run`). Upgrade for the message in place of the traceback; `--list` then leaves such a snapshot out.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/compare.py` — `_check_snapshot`, `is not a snapshot`

### `tjs compare` says "No difference" although a report file's bytes changed, when the file holds bytes that are not UTF-8 text
- **Check:** `cmp work/compare/prev/reports/NAME reports/NAME` finds a difference while `tjs compare` counts the file among the matching ones, and `file reports/NAME` says "data" or "with invalid UTF-8", not UTF-8 text. taxjson writes its reports as UTF-8 text, so such a file is damaged or was written by something else.
- **Cause:** the comparison decoded each report with every undecodable byte replaced by one placeholder character, so two files that differed only in such bytes read the same.
- **Fix:** upgrade: each undecodable byte stays distinct in the comparison (the `--details` diff shows them as `\xff` escapes). Then find out what wrote the file; `tjs run` rewrites the reports.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/compare.py` — `normalized`, `surrogateescape`

### "Error: 1 .tt ALLOWLOSS line(s) name no single denied superficial loss" — "1 units is one fill of the 2-unit sale of … (one same-day sell-down is one sale)" for a line dated a fill's settlement date
- **Check:** `tjs wash-sales`: the sale's fills were traded the same day but settle on different days.
- **Cause:** a line dated one fill's settlement date used to name that fill alone; a line names a whole sale or none, counted over all of the sale's fills.
- **Fix:** date the line with the sale's trade date (every fill matches it) and give the sale's total units, or none. A line without units dated such a settlement day names none too: "… is the settlement date of only some fills of the 2-unit sale … date the line YYYY-MM-DD".
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/loss_overrides.py` — `plan`, `fill_of`

### `tjs fx-cash --ledger v2`: "Warning: margin/ib: one FXCONV of 2024-12-30 is in 2 files with different settlement dates (a.csv settles it 2024-12-31; b.csv settles it 2025-01-02) — kept once, dated 2025-01-02; check which file is right"
- **Check:** open both named exports (or `.tt` files) at that date. The same conversion, move or fee shows the same amount and date in each, but different settlement dates.
- **Cause:** overlapping files of one account hold the same cash event once each. The ledger keeps it once, from one file, and used to drop the other file's settlement date silently.
- **Fix:** correct the file whose settlement date is wrong (re-export it, or fix the hand-written line). If they really are two events, give each a line that tells them apart.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/cash_events.py` — `dedup_across_files`, `collect`; `src/taxjson/lib/fx_cash_v2.py` — `render`

### `tjs fx-cash --ledger v2`: NOT COMPUTED with "inputs/margin/cash.tt: cannot be read (Permission denied)" (on an older release: "no opening pool" or "undeclared" problems and "inputs changed since the last full run (removed: inputs/margin/cash.tt)")
- **Check:** `ls -l inputs/<account>/` shows the file is not readable, or it is not UTF-8 (the message says "not UTF-8 text").
- **Cause:** the cash-line reader skipped a .tt file it could not read, so its CASHOPEN/CASHMOVE lines were missing and the ledger refused for the wrong reason.
- **Fix:** make the file readable (`chmod u+r`), or save it as UTF-8, then run `tjs run` and `tjs fx-cash --ledger v2` again.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/cash_events.py` — `read_project_lines`

### `tjs fx-cash --ledger v2`: a futures account's FX gain counts its opening commissions at the closing date's rate
- **Check:** a futures position was opened with a commission and closed after the exchange rate moved. `tjs fx-cash --ledger v2 --events` shows no cash leg on the opening date.
- **Cause:** ledger v2 took each futures fill's cash from its settled P/L, which folds the opening commission into the close, so that fee left the currency pool at the close date's rate.
- **Fix:** upgrade. Each fill's commission now leaves the cash on its own date, and the close carries its P/L before commissions; the books' gains are unchanged.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/bin/taxjson_fx_cash.py` — `_cash_legs`; `src/taxjson/lib/futures.py` — `settle_futures_dicts`, `_settle`

### US project: a futures gain differs from FIFO by trade date when a generic CSV maps a settle column whose futures settle dates are out of trade order
- **Check:** in `work/`, a futures fill traded later carries an earlier `date_settle` than an earlier fill of the same contract.
- **Cause:** the futures settlement consumed open contracts in settlement-date order, while the US engine (and US-FUT-01's FIFO) orders by trade date.
- **Fix:** upgrade, then run `tjs run` again. US futures FIFO now follows trade date; each fill keeps its own settlement date for its rate. Canada is unchanged.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/lib/futures.py` — `_settle`, `method_for`

### `tjs close-year` carries the whole year's net capital loss into the next year although part of it was carried back (`[carryover] claimed` under an earlier year); next year's `tjs carryover` shows that year "APPLIED" and the full balance left
- **Check:** in the closed year, `tjs carryover` shows a balance after the year lower than close-year's "carried into" line (or than `carryforwards.net_capital_loss.closing` in `filed/<year>.json`), and taxjson.toml has `[carryover] claimed` with an entry for one of the 3 years before it.
- **Cause:** the lock's balance came from the estimate, which does not read `[carryover] claimed`, so a T1A carry-back of the year's loss stayed in it; the next year's estimate and carryover then applied those dollars a second time. close-year now takes the carry-back `tjs carryover` finds out of the balance and says so ("carried back …").
- **Fix:** upgrade, then in the closed year `tjs close-year --force` to record the corrected balance; the next year's `tjs estimate` and `tjs handoff` read it.
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_carried_back`, `_carryforwards_for_lock`; `src/taxjson/bin/taxjson_carryover.py` — `build_canada_ledger`, `carried_back`

### Two loss years carried back to the same year: `tjs carryover` shows no carry-back in the second loss year, and `tjs close-year` carries its whole loss forward (no "carried back")
- **Check:** an earlier closed year's lock says "carried back" less than `[carryover] claimed` now records for the target year, and `tjs carryover` shows that closed year "APPLIED" the whole claim while its balance is the lock's.
- **Cause:** the ledger met the whole claim (the total applied on the earlier return) from the closed year, then took the balance its lock recorded, so the part of the claim that lock did not carry back was dropped instead of waiting for the later loss. A locked year now takes only the carry-back its lock recorded (CA-CARRY-04).
- **Fix:** upgrade; `tjs carryover` shows the rest of the claim carried back from the later year. If that later year is already closed, `tjs close-year --force` in it to record the lower balance.
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/bin/taxjson_carryover.py` — `build_canada_ledger`, `locked_back`, `lock_figure`

### `tjs close-year --filed-dispositions`: the lock carries forward the books' net capital loss, not the filed one; next year's `tjs carryover` row reads the filed loss but a balance that does not follow from it
- **Check:** close-year printed "As filed: … gain" different from its "Realized" line, and next year's `tjs carryover --details` says "these books rebuild" a balance other than the one the lock recorded.
- **Cause:** the lock's carry-forwards came from the year's estimate of the books' sales, while the filed dispositions were only stored for `tjs handoff`. Canada: the net capital loss now comes from the filed gain, and the summary shows both ("as filed: gain …; the books …") (CA-CARRY-01).
- **Fix:** upgrade, then `tjs close-year --force` in the closed year (it keeps the filed dispositions the lock recorded); the next year's `tjs estimate` and `tjs carryover` read the new balance.
- **Fixed in:** `v0.32.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_as_filed`, `_carryforwards_for_lock`, `filed_totals`

### `tjs checklist` shows no "Since then the inputs changed …" line after `--run --only ID` (or `--run --quick`) although ticker.map, a holdings snapshot or a slip changed since the other verdicts were made
- **Check:** `tjs checklist` after a `--run --only ID`: the "Checked …" line says nothing moved, while `ls -l` shows ticker.map, a file `holdings = [...]` names, a `holdings/` snapshot, `inputs/slips/` or `filed/` newer than the verdicts it shows (the oldest verdict's time is on the line).
- **Cause:** the kept verdicts carried one fingerprint, re-stamped with the current inputs by every `--run --only ID` / `--run --quick`, so the verdicts it did not refresh read as fresh; and the fingerprint covered what `taxjson run` reads only, never the positions snapshots `sanity` compares against, the slips or the filed-year locks.
- **Fix:** upgrade, then `tjs checklist --run` once. Each verdict now keeps its own basis (`work/checklist_results.json`, `bases`); a partial refresh leaves the others' as it was, and the line says how many of the verdicts shown are from before the change. The first `tjs checklist` after the upgrade lists the holdings and slip files as changed (they were not tracked before): the re-check clears it.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/checklist.py` — `save_results`, `_bases`, `_basis_now`, `_stale_reasons`, `checklist_fingerprint`, `_check_only_files`, `run_info_lines`

### `tjs checklist --run --only ID` kept another tax year's `done` verdicts, or shows `[-] n/a` on a step that applies now (an account made taxable)
- **Check:** `[settings] year` was bumped in place (or the project folder copied) and `work/` kept; or every account was sheltered when the step was checked and one is taxable now: the step (`t1135`, `audit`, …) stays `[-]` until a full `--run`.
- **Cause:** the store was merged over whatever it held, another year's verdicts included; and a step the project's shape made n/a was stored as such, then read back as a verdict once the shape changed (the shape is checked in the n/a direction only on load).
- **Fix:** upgrade, then `tjs checklist --run`. Another year's kept verdicts are dropped when this year's are stored; the shape's n/a is never stored and a store an older release wrote it into reads as "not checked yet".
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/checklist.py` — `_save_results_locked`, `_SHAPE_NA_DETAILS`, `load_results`

### `tjs checklist --done export-coverage` does not settle the step: "! export-coverage: … — not answered by the done mark of …" stays after the new gap was marked done
- **Check:** `tjs checklist --only export-coverage` (or `option-boundary`, `t5-t3`, `fx-cash`) shows the finding with "not answered by the done mark of <date>" although `--done` was just recorded for it; `work/checklist_results.json` has `"question": false` for the step.
- **Cause:** the `--run` stored the step as the list showed it, with the earlier mark's text on the detail and the question flag turned off, so no later mark could answer it until the next `--run`.
- **Fix:** upgrade, then `tjs checklist --run --only export-coverage` once (the store holds the detector's own verdict from then on); or `--run` again after each mark on an older release.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/checklist.py` — `apply_override`, `own_detail`, `own_question`

### `tjs checklist --done fx-cash` prints "fx-cash marked done", and "! 38 fx-cash: FX on foreign cash: NOT RELIABLE …" stays; `tjs close-year`: "1 checklist item(s) before the lock need attention" naming fx-cash
- **Check:** `tjs checklist --only fx-cash` shows "marked done" with "the detector still says: FX on foreign cash: NOT RELIABLE …", and exits 1; `checklist.json`'s `fx-cash` entry has no `"forced": true` (also after `--done fx-cash --force`, which printed no "(forced)"). The same for option-boundary's "contract(s) require a T1-ADJ" and for a blocked export-coverage, option-boundary, t5-t3 or fx-cash step.
- **Cause:** a done mark never hides a finding, but the four steps that ask a question (export-coverage, option-boundary, t5-t3, fx-cash) took a plain `--done` whatever their verdict, without the hint other steps print, and `--force` never forced them: the command went by the step, not by whether its kept verdict was a question. A finding that asks nothing (the default ledger's NOT RELIABLE, an amendment, a check that could not run) is not answered by a mark.
- **Fix:** upgrade, then `tjs checklist --done fx-cash --force --note "..."`: it replaces the plain mark with the forced one (shown `[f]`, the finding beside it), and `tjs close-year` accepts it; a plain `--done fx-cash` over the finding is refused, naming `--force`. A question awaiting its answer (fx-cash's v2 figure to review, an export gap) is still answered by a plain `--done`. On an older release: `tjs checklist --skip fx-cash`, or `tjs checklist --walk --only fx-cash` and the key for accepting it as it is.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/checklist.py` — `finding_over_done`, `kept_verdict`, `QUESTION_STEPS`; `src/taxjson/bin/taxjson_run.py` — `cmd_checklist`, `_checklist_walk`

### `tjs checklist --done ID --run --only typo` (or `-j 0`, or `--walk` without a terminal) records the mark, then stops with "unknown step 'typo'"
- **Check:** `checklist.json` holds the mark although the command ended in an error.
- **Cause:** the marks were written before the rest of the command line was checked.
- **Fix:** upgrade: the whole command line is checked first, and nothing is written on an error. On an older release, `tjs checklist --undo ID` takes the mark back.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_checklist`, `writes no mark`

### `tjs checklist --skip ID`: "Error: unrecognized arguments: --skip ID" (or "Error: --skip was replaced by --done --force: `taxjson checklist --done ID --force`")
- **Check:** `tjs --version` is v0.35.0 or newer; `tjs checklist --help` lists `--done ID[,ID]` and `--force`, and no `--skip`.
- **Cause:** v0.35.0 replaced `--skip ID` with `--done ID --force`: a done mark over the check's finding, shown `[f]` with the finding beside it. v0.35.0 refused the old flag with argparse's bare message, which does not name the new form.
- **Fix:** `tjs checklist --done ID --force` (`--note "..."` stores your reason); in a script, write each `--skip ID` that way. The `skipped` marks already in `checklist.json` need nothing: they read as forced. Upgrade: `--skip` names its replacement (exit 2, nothing written).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_checklist`, `--skip was replaced by --done --force`

### `tjs checklist`: "AttributeError: 'int' object has no attribute 'get'" with an `[accounts]` entry that is no table (`margin = 42`)
- **Check:** taxjson.toml has a value under `[accounts]` that is not an `[accounts.<name>]` table; every other command says "[accounts.margin] must be a table".
- **Cause:** the checklist reads a refused taxjson.toml leniently (so the configure step can say what is wrong) and kept the entry, which the checks then read as a table.
- **Fix:** make it a table (`[accounts.margin]` with `type = "taxable"` or `"sheltered"`); upgrade: the checklist stops with the loader's own message.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_checklist_config`; `src/taxjson/lib/checklist.py` — `accounts`, `_project_shape`

### `tjs checklist` ran, and wrote `work/checklist_results.json` through a `work/` link to outside the project, where every other command stops with "folder(s) that are symlinks to outside the project"
- **Check:** `ls -ld work` shows `work -> /somewhere/outside`; `tjs checklist --run` showed the list (the configure step naming the link) and the verdicts file appeared in the link's target.
- **Cause:** the lenient read of a refused taxjson.toml (the configure step's finding) skipped the loader's refusal of such links, and the verdicts were stored through it.
- **Fix:** replace the link with a real folder (`rm work && mkdir work`); upgrade: the checklist refuses the link as every command does (exit 2), and the store itself never writes through one.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_checklist_config`, `_refuse_outside_dir_links`; `src/taxjson/lib/checklist.py` — `save_results`, `link_outside`

### `tjs checklist --walk` exits 0 while a step still needs attention
- **Check:** the summary the walk ends with still lists a `!` step (a question step marked done for other questions than the ones asked, a finding passed over), and `echo $?` is 0.
- **Cause:** the walk's exit counted the steps passed over and a quit only, not the verdicts with the marks applied.
- **Fix:** upgrade: the walk exits 1 while anything is open, as `--run` does.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_checklist_walk`, `follows the verdicts as kept`

### Two `tjs checklist --run --only ID` at once: one's verdict is missing afterwards
- **Check:** `work/checklist_results.json` holds the step of one run only, both runs having exited cleanly.
- **Cause:** each read the kept verdicts, merged its own and wrote the whole file; the second write replaced the first's.
- **Fix:** upgrade: the read, merge and write hold the project lock (the one `--done` uses). Run the missing step again.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/checklist.py` — `save_results`, `_StateLock`

### `tjs handoff`: "this project does not have it: it is in neither return. Add it here." for an early-January purchase the books hold, after the broker corrected its price
- **Check:** `tjs list <account>` shows the row (same account, date and quantity) at the corrected amount; the handoff line gives the amount the closed year's project read.
- **Cause:** a closed year's early-January row was matched with this project's on its amount too (within 0.5%), so a corrected fill read as a different row; adding it as told would have booked the purchase twice.
- **Fix:** upgrade, and do not add the row. `tjs handoff` now takes the row of the same account, action, symbol, date and quantity, on the same side of Dec 31, as that row, and notes both amounts ("… when the 2023 project read it (the export was corrected) — both count it in 2024, so it is in this return once").
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/handoff.py` — `_same_row`, `_boundary_issues`, `the export was`

### `tjs handoff`: the cost choice says "or amend 2024 and open with" the cost in the 2024 books; `tjs handoff --prior` with a record two years back says "into 2024 (this project)" in a 2025 project
- **Check:** in the cost-difference line, the amount after "amend 2024 and open with" is the "in the 2024 books" figure, not the "here" one.
- **Cause:** the amend choice printed the closed record's cost instead of the corrected one, and the header took the record's year + 1 for this project's year.
- **Fix:** upgrade: "keep 2024 as filed and open with the cost that return implied (the record's cost), or amend 2024 and open with" the corrected cost here; the header names this project's year.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/handoff.py` — `check`, `render`, `cost that return implied`

### `tjs handoff`: "[carryover] claimed 2023  here: 400.00  2023 record: 0.00" for a T1A carry-back of this year's loss, and `tjs close-year` stops on handoff; `tjs estimate` says the whole loss carries forward
- **Check:** this year has a net capital loss, `[carryover] claimed` records the carry-back under the year before, and `tjs carryover` shows this year APPLIED it with a balance lower than the estimate's "Unused capital losses".
- **Cause:** handoff compared the whole claim under the closed year with what that year's record applied, but the record was closed before this year's loss existed: the part carried back from this project's loss cannot be in it. The estimate's unused losses never read `[carryover] claimed`.
- **Fix:** upgrade. handoff takes the carry-back `tjs carryover` finds out of the claim before comparing (its why names it: "carried back from this project's loss, T1A"); a claim that also differs from what the record applied is still flagged. The estimate reads "Unused capital losses: 100.00 (carry forward; 400.00 carried back: [carryover] claimed, T1A)", what close-year carries forward.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/carryforward.py` — `handoff_issues`; `src/taxjson/bin/taxjson_run.py` — `_carried_back_to`, `_claims_back`, `_ledger_rows`, `cmd_handoff`, `_print_tax_estimate`; `src/taxjson/bin/taxjson_carryover.py` — `build_canada_ledger`, `carried_back_to`

### `tjs carryover`: a closed year shows APPLIED 0.00 while its balance falls, and "! 2025: apply up to … of the carryforward" although its lock applied it
- **Check:** the year's lock (`filed/2025.json`) has `carryforwards.net_capital_loss.applied` above 0, and `[carryover] claimed` has no entry for 2025.
- **Cause:** APPLIED came only from `[carryover] claimed` while the balance came from the lock, so the row did not add up, and the gain the lock had already sheltered was offered again (to apply, and as carry-back room).
- **Fix:** upgrade: such a year's APPLIED is what its lock applied, and none of that gain is offered again. A `claimed` entry for the year still says what the return claimed when it differs.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_carryover.py` — `build_canada_ledger`, `lock_figure`, `locked_applied`

### `tjs carryover`: the year's NET GAIN(LOSS) is a cent or two off the RETURN row of `tjs sum`, and in a loss year its carryforward a cent off what `tjs estimate` and `tjs close-year` carry forward
- **Check:** `tjs carryover --json` on an older release has no `net_source` on the project year's row; `tjs sum --details` names the gains files' unrounded total, the ledger's figure.
- **Cause:** the ledger summed the engine's unrounded gains; the RETURN row, which the estimate taxes and close-year's lock carries forward, adds each Schedule 3 row's cents.
- **Fix:** upgrade (Canada): the project year's NET GAIN(LOSS) is the RETURN row (`--json`: `net_source` `"return_row"`, the engine's total as `engine_net_gain`; `--details` names it), so the balance, the T1A carry-back close-year records and `tjs handoff`'s check follow the return. A year before the project year takes the RETURN row its lock recorded (`return_row`), else the lock's Schedule 3 gain lines.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_carryover.py` — `main`, `lock_figure`, `engine_net_gain`, `return-row`; `src/taxjson/bin/taxjson_run.py` — `cmd_carryover`, `_project_return_row`, `_return_row_totals`

### `tjs estimate --json`: `losses_unused` is the whole loss although the estimate says part of it is carried back (T1A)
- **Check:** the console reads "Unused capital losses: … (carry forward; … carried back: [carryover] claimed, T1A)".
- **Cause:** only the console took the carry-back out; the JSON had no key for it.
- **Fix:** upgrade (Canada): `estimate` carries `losses_carried_back` and `losses_carried_forward` (the console's two figures); `losses_unused` keeps its value, the figure before the carry-back.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `cmd_summary`, `_losses_carried_back`, `losses_carried_forward`, `_print_tax_estimate`

### `tjs carryover --details`: "what you claimed on filed returns may differ: --claimed FILE", and `tjs carryover --claimed FILE` is refused ("unrecognized arguments")
- **Check:** `tjs carryover --details` names `--claimed`.
- **Cause:** the command reused the notes of the standalone `taxjson-carryover`, which takes `--claimed FILE`.
- **Fix:** upgrade: the notes name taxjson.toml. Record the claims there: `[carryover]` then `claimed = { 2023 = 400.00 }` (docs/commands.md, `taxjson carryover`).
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_carryover.py` — `render`, `claims-in-project`; `src/taxjson/bin/taxjson_run.py` — `cmd_carryover`

### `tjs estimate`: INSTALMENTS says "=> balance due April 30" for an amount far below the year's tax
- **Check:** the amount is `tjs instalments`' shortfall: the instalments required less those paid, not the tax still owed.
- **Cause:** the instalments still behind were labelled as the balance due.
- **Fix:** upgrade: the line reads "=> instalments behind … [due with the balance April 30; taxjson instalments]". What is owed at filing is the year's tax less the instalments paid and the tax withheld.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_run.py` — `_print_tax_estimate`, `instalments behind`

### `tjs amt` / `tjs estimate`: "Recovered: ON share 346.00 [24.63% of the federal amount, before ON surtax]" is more than 24.63% of the federal amount
- **Check:** 24.63% of "Recovered: federal (line 40427)" is less than the ON share; the rest is about 56% of it, the Ontario surtax.
- **Cause:** the ON share was the whole drop in Ontario tax, the surtax it saves included, under a label for the share off basic tax.
- **Fix:** upgrade: "Recovered: ON share" is the factor times the federal amount, deducted from basic Ontario tax (the provincial form's carryover line), and the surtax it saves is a row of its own; the estimate reads "[federal 900.50 + ON 221.79 + ON surtax 124.21]". The total recovered and the tax are unchanged. `tjs amt YEAR` on a lock closed by an older release shows its one figure as "provincial tax (surtax included)".
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/tax_estimate.py` — `recovered_provincial_credit`, `recovered_provincial_surtax`; `src/taxjson/lib/amt_report.py` — `render`, `render_recorded`; `src/taxjson/lib/carryforward.py` — `record_block`

### `tjs check-filed`: "filed 2024: OK" for a lock whose `fx_cash_v2` close the next year's FX-on-cash ledger refuses; the ledger's repair names filed/2025.json although `prior_year_record` names another file
- **Check:** the next year's `tjs fx-cash --ledger v2` says "the prior-year record is damaged".
- **Cause:** the lock check did not look at the `fx_cash_v2` close (not a locked figure), and the repair always named `filed/<year>.json`.
- **Fix:** upgrade: `tjs check-filed` names "fx_cash_v2 is not a table of books" (or the book with no finite units and cost) and exits 1; restore the lock from git, or re-close that year with the v2 ledger (`tjs close-year --force`). The repair names the record `[settings] prior_year_record` gives.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/lib/fx_cash_v2.py` — `close_problem`, `_carry_openings`; `src/taxjson/bin/taxjson_filed.py` — `lock_damage`; `src/taxjson/bin/taxjson_run.py` — `_fx_cash_carry_record`

### `tjs option-boundary`, `tjs compare filed` or the `option_grant_timing_since` hint read a damaged lock, or one closed under the other country, as a good one; `tjs years` shows such a lock as a clean filed year
- **Check:** `tjs check-filed` names the lock as damaged or closed under the other country.
- **Cause:** these readers parsed the lock as plain JSON for its option timing or reports, without the check every other lock reader applies; `tjs years` judged the lock's shape but not its country.
- **Fix:** upgrade: option-boundary warns "cannot read filed/2024.json as a close-year lock — its recorded option timing is unknown", `tjs compare filed` stops naming the problem, the hint skips that lock, and `tjs years` shows the year "other country". Restore the lock from git, or re-close the year. `tjs carryover` only warns (exit 0) about a damaged lock of the project year or a later one: that row comes from the books, the lock gives it no figure, and check-filed checks the lock.
- **Fixed in:** `v0.36.0`
- **Code:** `src/taxjson/bin/taxjson_filed.py` — `read_lock`, `lock_country_problem`; `src/taxjson/bin/taxjson_run.py` — `cmd_option_boundary`, `_locked_grant_since`, `_years_table`; `src/taxjson/lib/compare.py` — `load`; `src/taxjson/lib/option_boundary.py` — `filed_locks`; `src/taxjson/lib/project_layout.py` — `years_report`

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

### `tjs wash-radar --date 9999-12-31`: "error: --date 9999-12-31 is out of range: the view looks 60 days either side of it; use a date from 0001-03-02 to 9999-10-31"
- **Check:** the date given to `tjs wash-radar` (or `taxjson-safe-to-sell`) is within 60 days of the first or last date Python can hold. On v0.28.1 and older it stopped with "ValueError: year 10000 is out of range" (issue #54).
- **Cause:** the radar looks 30 days either side of the date (more with settlement days), and those dates do not exist.
- **Fix:** give the date you meant (`--date YYYY-MM-DD`), or leave it out for today.
- **Fixed in:** `v0.28.2`
- **Code:** `src/taxjson/lib/dates.py` — `as_of_date_problem`; `src/taxjson/bin/taxjson_run.py` — `cmd_wash_radar`; `src/taxjson/bin/taxjson_wash_radar.py` — `main`; `src/taxjson/bin/taxjson_safe_to_sell.py` — `main`

### `taxjson-generate-parser sample.csv`: "error: the first 30 line(s) of sample.csv still carry personal data (1 identity row(s)/cell(s) (name, alias, address), the redactor changes line 1) and would be sent to claude", or "(1 line(s) the redactor could not classify (line 2))"
- **Check:** run `taxjson redact --check sample.csv`: it reports the same findings and lists the REVIEW lines by number. On v0.29.0 and older a sample whose first line was the holder's name alone (a statement preamble), or whose Description held a name-like phrase, was sent to the provider as it was (issue #80).
- **Cause:** the gate kept its own list of the redactor's counters and missed identity rows (a bare preamble name, an alias or address cell) and the review list, then sent the file's own text. Now it counts every kind the redactor reports (`Report.identity_counts`), refuses when the redactor would change any line beyond a transaction-id pseudonym, and sends only the redactor's copy of a sample that passed.
- **Fix:** `taxjson redact sample.csv` and use its output. A redacted copy can still list REVIEW lines (free text the redactor cannot classify, such as a security named like a person): read those lines, and if they hold no personal data pass `--allow-unredacted` (the file is then sent as it is).
- **Fixed in:** `v0.29.1`
- **Code:** `src/taxjson/bin/taxjson_generate_parser.py` — `gate_sample`, `identity_findings`, `main`; `src/taxjson/bin/taxjson_redact.py` — `Report.identity_counts`, `IDENTITY_KINDS`, `UNREPLACED_REASON`

### `taxjson-export h.toml --report`: an option's cost/share or `contract_multiplier` changes when the holdings TOML is read back
- **Check:** the `[[holding]]` has a `contract_multiplier` other than 100.
- **Cause:** the TOML reader dropped `contract_multiplier`, so the default 100 (or unknown, for a futures option) was used.
- **Fix:** upgrade.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/bin/taxjson_export.py` — `_holdings_toml_to_inventory`, `contract_multiplier_of`

### `taxjson-convert-currency book.json --to CAD --country canada`: "Error: futures book mixes 1 row(s) already on the settlement basis (e.g. F:ZZH6.US 2026-01-01 (id=…)) with 1 unsettled fill(s) (e.g. F:ZZM6.US 2026-01-02 (id=…)): a book is settled once, as a whole"
- **Check:** the book holds plain-futures rows of `type` `futures_settlement` (a converted book: `work/<account>_base.json`) and, in the same file, a `BUYSELL` fill of a futures contract without that type (a raw export's row, a `.tt` line).
- **Cause:** the settlement pass is idempotent — a book already rewritten comes back as is — but it decided that on the first settled row it met and returned the whole book, so a raw fill beside it kept its notional as its money, which the conversion then translated at the day's rate: the FX-on-notional error the settlement basis exists to prevent (audit 2026-10-24 B09). A mixed book is refused now; a settled book and a raw book are handled as before. `taxjson run` never writes a mixed book; one comes from rows appended to a converted file by hand.
- **Fix:** keep the raw fills in a book of their own and convert that; in a project, put them in `inputs/<account>/` (an export or a `.tt` file) and `tjs run`, which settles and converts them with the rest.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/futures.py` — `_settle`, `futures book mixes`

### `taxjson-sum-income book.json`: "Error: book.json: transaction at index 0 (id='?', symbol='ZZQ.US'): non-finite amount=nan — fix the input data" (or "non-numeric amount='ten'", "amount=True is not a number"); or, on an older release, a CASH INTEREST of `nan`, or an `AttributeError: 'NoneType' object has no attribute 'lower'` traceback on a row whose `type` is null
- **Check:** the row the message names: its `amount` (the alias of `net_amount` / `gross_amount` the income summary also reads) is `"NaN"`, text or a boolean. The traceback: a row with `"type": null`.
- **Cause:** every row goes through the shared row funnel (a NaN, inf or text amount is refused as `taxjson-gains` refuses it), but `amount` is not a field of the funnel's transaction, so it was dropped before the check and reached the totals as `nan` (audit 2026-10-24 B49); and the funnel's normalized row (a null `type` read as the default, a numeric string as its number, a date padded to YYYY-MM-DD) was thrown away, so the summary read the file's own values and crashed on the null (B46). The summary reads the normalized row now, and `amount` is checked like the other amounts. An amount written `null` stays null (the normalized row's value for it is a default 0.00, which once booked a dividend, tax or interest row with null `gross_amount` and `net_amount` as $0 at exit 0; 2026-10-25 audit diff-tooling#3).
- **Fix:** correct the amount named (a number), or remove the key: a row with no amount at all (each amount key absent or `null`) is refused with its own line ("income row(s) carry no amount", exit 2).
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_sum_income.py` — `load_income_data`, `_checked_amount`

### `taxjson-sum-gains gains.json`: "Error: gains.json: "transactions" row 0 (ZZQ.US): dividend is inf, a non-finite number — the file is damaged or hand-edited: fix it or re-run `taxjson run`"; or, on an older release, "TOTAL DIVIDENDS / STAKING: inf CAD" and an infinite grand total at exit 0
- **Check:** the row the message names: its `dividend` (or `pil`) is a number too large for a float (`1e400` is valid JSON). Only a hand-written file holds one; the engines never write it.
- **Cause:** the row funnel every report reader runs checked the disposition fields (qty, cost, proceeds, gain, …) for finite numbers but not the income fields a gains file carries beside them, so the row was summed and the text report printed `inf` (`--json` refused the output; audit 2026-10-24 D03). `dividend` and `pil` are checked now, in every reader.
- **Fix:** correct the amount, or `tjs run` to rebuild the file.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/lib/json_input.py` — `_NUM_FIELDS`, `check_row_types`

### `taxjson-sum-gains 2025/work/margin_gains.json 2026/work/margin_gains.json`: "Error: the gains files are of different tax years: 2025/work/margin_gains.json (2025), 2026/work/margin_gains.json (2026) — a summary is one year's figures: run it on each year's files on their own"
- **Check:** each file's `summary.year`: the files given are of two tax years.
- **Cause:** the tool sums every file's rows into one report and took the LAST file's year as the report's, so two years' gains were labelled as one year's total, and which year depended on the order the files were given (audit 2026-10-24 D04). Files of different years are refused now. A file with no `summary.year` (a hand-run `taxjson-gains` output) takes the year of the files that have one, as before.
- **Fix:** run it on one year's files at a time (in a project, `tjs sum` in the year's folder).
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_sum_gains.py` — `main`, `different tax years`

### `taxjson-diff old.json new.json` says "0 modified" when a record's `cost` (or another number) went from `null` to `0`
- **Check:** the same record in the two files (matched by action, date, time, symbol, quantity, price and account) has `"cost": null`, or no `cost` key, on one side and `"cost": 0.0` on the other; an older release printed `0 modified` for it.
- **Cause:** a missing number beside a float was read as 0 before the tolerance was applied, so an unknown value and a zero compared equal. An unknown cost is the engine's "no basis" (such a disposition goes to `manual_reporting_required`); a cost of 0 is a basis of zero.
- **Fix:** upgrade: a missing value (`null` or `""`) beside a number is a difference, shown as `- cost: ` / `+ cost: 0.0000`. `null` and `""` are still one another, and `0` and `0.0` are still equal.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_diff.py` — `values_equal`, `_missing`

### `taxjson-diff old.json new.json --by symbol,wash_replacements` stops with a traceback ending "TypeError: unhashable type: 'list'" (or 'dict')
- **Check:** a field named in `--by` holds a list or an object in the records (`wash_replacements`); the traceback passes through `make_key`. The same files compare when `--by` names plain fields only.
- **Cause:** the match key was built from the fields' values as they are, and a list or an object cannot be part of a key.
- **Fix:** upgrade: a structured field is matched on its canonical JSON text (an object's key order does not matter). On an older release, leave such fields out of `--by`.
- **Fixed in:** `v0.31.1`
- **Code:** `src/taxjson/bin/taxjson_diff.py` — `make_key`

### `taxjson-diff old.json new.json`: "cannot read input old.json: "manual_reporting_required"[0] is a number, not an object (a record)" (exit 2); an older release gave "0 added | 0 removed | 0 modified" at exit 0 for the same files
- **Check:** open the named file at that index of its `manual_reporting_required` (or `transactions`) list: the entry is not a JSON object (a number, text, a list), or the section is not a list at all. taxjson's own gains files hold objects there; the file was hand-edited or damaged.
- **Cause:** an entry of `manual_reporting_required` that was not an object was skipped without a word, so a file whose section held junk compared equal to one whose section was empty.
- **Fix:** upgrade, then repair the entry (or `tjs run` to rebuild a work file) and diff again.
- **Fixed in:** `v0.35.0`
- **Code:** `src/taxjson/bin/taxjson_diff.py` — `_rows`, `extract_records`, `main`

## Releasing and landing (maintainers)

### `scripts/promote.sh vX.Y.Z`: "vX.Y.Z is not published yet — the publisher publishes it after its wait; promote it then."
- **Check:** `git ls-remote public refs/tags/vX.Y.Z` (the public repo; empty: not published) and `git ls-remote origin refs/tags/vX.Y.Z` (the dev repo has it). The publisher's issues say whether the release is waiting (its digest names the time), blocked by the gate, or rejected.
- **Cause:** a tag reaches the public repo only through the publisher: it gates the tag, waits an hour, gates it again and publishes it, on a schedule that GitHub may start hours late (docs/releasing.md, "How a release reaches the public repo"). A channel may name only a published release.
- **Fix:** wait for the publish, or start a publisher run by hand once the wait is over; a release blocked by the gate never publishes: fix it and release the next patch version. Then promote.
- **Fixed in:** —
- **Code:** `scripts/promote.sh` — `is not published yet`; `scripts/release.sh` — `tag_free`

### `scripts/release.sh` or `scripts/promote.sh`: "no remote 'public' for the public repo — add it, read only: …"
- **Check:** `git remote -v` in the clone: `origin` should be the private dev repo and `public` the public repo with the push URL `DISABLED`.
- **Cause:** both scripts read the public repo (a release tag name it has is used for good; a channel may name only what it has) through the remote `public`, which a fresh clone of the dev repo lacks.
- **Fix:** `git remote add --no-tags public https://github.com/taxjson/taxjson.git`, then `git remote set-url --push public DISABLED`, and run the script again. `--no-tags` matters: the public repo's tags are the publisher's own objects and would clash with the dev repo's.
- **Fixed in:** —
- **Code:** `scripts/release.sh` — `PUBLIC_REMOTE`; `scripts/promote.sh` — `PUBLIC_REMOTE`, `fast_lane`

### Dev repo CI: every job says "Waiting for a runner to pick up this job" (or "The job was not started because recent account payments have failed or your spending limit needs to be increased")
- **Check:** `gh api repos/<dev repo>/actions/runners --jq '.runners[] | "\(.name) \(.status)"'`: are the `taxjson-dev` runners online? On the maintainer's machine, `systemctl status 'actions.runner.*'`. The second message means a job asked for GitHub's runners in the dev repo: `runs-on` in `.github/workflows/tests.yml`.
- **Cause:** the dev repo is private, so its jobs run on its own self-hosted runner (label `taxjson-dev`); GitHub's runners for private repos draw on the account's included minutes, which the full matrix twice per landing used up in a day. A job waits while no runner with that label is online (the machine off, the service stopped, the runner removed), and GitHub cancels it after 24 hours.
- **Fix:** start the runners (`sudo systemctl start 'actions.runner.*'`), or set them up again with the publish kit's `runner/setup.sh <dev repo>`; waiting jobs then run. If `scripts/land.sh` timed out meanwhile, follow its message once the checks pass.
- **Fixed in:** —
- **Code:** `.github/workflows/tests.yml` — `taxjson-dev`, `RUNNER_ARCH`, `RUNNER_TEMP/venv`

### `scripts/release.sh vX.Y.Z`: "GitHub CI has not passed on the version commit … — nothing tagged" (after "No tests.yml run for the version commit of vX.Y.Z (…) on a push to main", or "CI failed on the version commit of vX.Y.Z (failure): https://github.com/…/actions/runs/…")
- **Check:** `gh run list --workflow tests.yml --branch main --event push` — is there a run for the version commit (`git log -1 --format=%H -G'^version = ' -- pyproject.toml`), and did it pass? Was the version commit the head of its push?
- **Cause:** the cut tags only a version commit whose own push to `main` passed `tests.yml` (a pull request's run does not count). No run: the commit reached `main` inside a larger push (GitHub runs on a push's head only) or not at all — `scripts/release.sh vX.Y.Z --prepare` makes and lands it. A failed run: the matrix found something the local gate did not.
- **Fix:** a flaky run: re-run it on GitHub (`gh run rerun <id> --failed`) and cut again. A real failure: fix it on `main` (land the fix), prepare the next patch version and cut that; the failed version is never tagged.
- **Fixed in:** `v0.28.3`
- **Code:** `scripts/release.sh` — `github-ci.sh`, `tag_free`; `scripts/github-ci.sh` — `No tests.yml run for`, `CI failed on`

### `scripts/release.sh vX.Y.Z` or `scripts/promote.sh`: "CI for … is still running — waiting for it", then at once "CI failed on …" while the run is still going
- **Check:** `gh run view <id>` (the address in the message): the run is queued or in progress, not failed. `gh run watch <id> --exit-status` with the same token says `failed to get annotations: HTTP 403: Resource not accessible by personal access token`.
- **Cause:** `scripts/github-ci.sh` waited for a running run with `gh run watch`, which reads check-run annotations; a fine-grained token cannot, so watch exited non-zero at once and every run still going was reported failed. A finished run was read through `gh api` and judged right.
- **Fix:** upgrade: it polls the run through `gh api` (`TAXJSON_CI_POLL_SECS`, default 10). Before that: wait for the run to finish, then run the cut (or promote) again.
- **Fixed in:** `v0.31.1`
- **Code:** `scripts/github-ci.sh` — `actions/runs/$id`, `TAXJSON_CI_POLL_SECS`

### `scripts/land.sh`: "Check(s) failed: … — the pull request is closed and its branch deleted; the commits stay on local main: fix, commit, and land again." (also from `release.sh --prepare` and `promote.sh`)
- **Check:** open the named pull request's checks on GitHub; locally `git log origin/main..main` shows the commits that did not land.
- **Cause:** `main` takes changes by pull request once its checks pass. A failed check turns auto-merge off and closes the pull request, so nothing merges later behind your back. `promote.sh` then takes its commit back; `release.sh --prepare` leaves the version commit on local `main`.
- **Fix:** fix the cause in a new commit and run `scripts/land.sh` again (for a release: then `scripts/release.sh vX.Y.Z --prepare` says it is prepared once it has landed, and the cut follows). A flaky check: land again as is. An emergency with the rule switched off: docs/releasing.md, "Emergencies".
- **Fixed in:** `v0.28.3`
- **Code:** `scripts/land.sh` — `fail`, `Check(s) failed`, `gh pr close`

### `taxjson reconcile-slips inputs/slips/*.csv` says "no slip CSV matches inputs/slips/*.csv: nothing to reconcile yet"
- **Check:** `ls inputs/slips/` lists no `.csv` file (an older release printed `taxjson-reconcile-slips: no such file: inputs/slips/*.csv`, the unexpanded pattern, with no label and no fix).
- **Cause:** the checklist's and filing.md's command names the slip files by a pattern; with none saved yet the shell passes the pattern through as a file name.
- **Fix:** save the broker's T5008 / 1099-B slip CSV in `inputs/slips/` (docs/filing.md); with no slip to reconcile, mark the step done: `taxjson checklist --done t5008` (the step is to do, not flagged, so it needs no `--force`).
- **Fixed in:** `v0.31.0`
- **Code:** `src/taxjson/bin/taxjson_reconcile_slips.py` — `no slip CSV matches`

### `scripts/land.sh` says "Check(s) failed" at once, before the new pull request's checks have started (landing the same commit again)
- **Check:** does the pull request's head commit match the one a closed pull request had (`gh pr list --state closed --search <sha>`)? On GitHub, the named checks link to an older run.
- **Cause:** `gh pr checks` lists the head commit's checks, and until the new pull request's run exists those are the last run's on that commit. A commit landed again unchanged (after a runner or minutes problem, not a code fix) showed the earlier pull request's failures, and `land.sh` closed the new one at once.
- **Fix:** update; `land.sh` now counts an Actions check only when its run is on the branch it pushed (the runs of `branch=` that branch and `head_sha=` the pushed commit), and waits until that run appears. On an older copy, make the commit new (`git commit --amend --no-edit`) and land again.
- **Fixed in:** —
- **Code:** `scripts/land.sh` — `checks`, `head_sha`

### `scripts/land.sh` waits until its timeout though the pull request's checks passed
- **Check:** `gh pr checks <number> --repo cksci/taxjson-dev` says "Resource not accessible by personal access token"; `gh auth status` shows a `github_pat_` token.
- **Cause:** `gh pr checks` reads the check-runs API, which needs a permission ("Checks") that fine-grained personal tokens cannot have; the machine's everyday token is one (publish kit D8). An older `land.sh` saw no checks and kept waiting.
- **Fix:** update: `land.sh` reads the jobs of the pushed commit's Actions runs on its branch, and the commit's statuses (the token's Actions and Commit statuses read access). To land a pull request already waiting: `gh pr merge <url> --rebase --delete-branch` once its run passed, then `git pull --rebase`.
- **Fixed in:** — (2026-10-17)
- **Code:** `scripts/land.sh` — `checks`

### `scripts/land.sh`: "main moved on while the checks ran (origin/main is …; they ran on …): … — the pull request is closed and its branch deleted; the commits stay on local main: git pull --rebase, run the gate again, and land again." (also from `release.sh --prepare` and `promote.sh`)
- **Check:** `git fetch origin && git log --oneline HEAD..origin/main`: the commits that reached `main` during the wait (another pull request, a promote from another clone, a merge on the web).
- **Cause:** `land.sh` rebase-merges only onto the base its commits were gated on (docs/releasing.md, "Why a rebase merge"). GitHub's rebase merge would replay them onto the newer `main`, and the landed commits would be trees no gate ran; an older `land.sh` checked `main` only before it pushed, and merged anyway.
- **Fix:** `git pull --rebase`, run the gate on the result (`scripts/ci.sh`), and land again. For a release: the version commit is rebased too; run `scripts/release.sh vX.Y.Z --prepare` again, which lands it.
- **Fixed in:** `v0.36.0`
- **Code:** `scripts/land.sh` — `main moved on while the checks ran`, `origin_main`, `BASE`

### `scripts/ci.sh` in a fresh `git clone`: "no venv at ./venv (venv/bin/python3): run scripts/dev-setup.sh here, or set PYTHON=" (an older copy: "lint: FAILED (ruff is not installed: pip install -e ".[dev]")", then "venv/bin/python3: No such file or directory" for every stage)
- **Check:** `ls venv/bin/python3` in the clone: a fresh clone has no venv; `echo $PYTHON`.
- **Cause:** the gate runs every stage with `$PYTHON`, else the clone's own `venv/bin/python3`, which only `scripts/dev-setup.sh` makes. An older `ci.sh` did not check, so each stage failed and the lint stage blamed a missing ruff.
- **Fix:** run the gate on the clone's code with your dev checkout's venv: `PYTHONPATH=$PWD/src PYTHON=<dev checkout>/venv/bin/python3 scripts/ci.sh` (a full PASS is then recorded for `release.sh` to reuse: docs/releasing.md, "Reusing a PASS"), or run `scripts/dev-setup.sh` in the clone first.
- **Fixed in:** `v0.36.0`
- **Code:** `scripts/ci.sh` — `no venv at ./venv`, `cannot be run`

### `scripts/ci.sh`: "lint: note: ruff 0.17.0 here, 0.17.1 in requirements/dev.txt (what CI lints with)", or CI's `ruff (critical tier)` fails though the local lint passed
- **Check:** `venv/bin/python -m ruff --version` against the `ruff==` line of `requirements/dev.txt`.
- **Cause:** CI's lint job and `scripts/dev-setup.sh` install ruff from the dev lock; a venv set up before the lock moved (or before both read the lock: CI pinned one version, the lock another, and `dev-setup.sh` took the newest) lints with another version.
- **Fix:** re-run `scripts/dev-setup.sh` (or `venv/bin/pip install --require-hashes --only-binary :all: -r requirements/dev.txt`), then the gate.
- **Fixed in:** `v0.36.0`
- **Code:** `scripts/ci.sh` — `RUFF_LOCK`, `what CI lints with`; `scripts/dev-setup.sh` — `requirements/dev.txt`; `.github/workflows/tests.yml` — `-r requirements/dev.txt`

### `scripts/release.sh vX.Y.Z` or `scripts/promote.sh`: "CI for … is still queued after 60 min (TAXJSON_CI_WAIT_MINS): https://github.com/…/actions/runs/… — is the runner online?" (an older copy waited with no end)
- **Check:** the run's address: queued (no runner has taken it) or in progress? `gh api repos/<dev repo>/actions/runners --jq '.runners[] | "\(.name) \(.status)"'` — are the `taxjson-dev` runners online (the entry "every job says Waiting for a runner" above)?
- **Cause:** `scripts/github-ci.sh` waits for a CI run still going before the cut tags or a promote moves a channel. A run waits in `queued` for as long as the self-hosted runner is offline; an older copy polled it with no time limit (and gave up at one failed poll), so the cut or the promote hung without a word.
- **Fix:** start the runner, let the run finish, and cut (or promote) again. A slow run: `TAXJSON_CI_WAIT_MINS=120 scripts/release.sh vX.Y.Z`. A failed poll is tried again, up to five in a row.
- **Fixed in:** `v0.36.0`
- **Code:** `scripts/github-ci.sh` — `TAXJSON_CI_WAIT_MINS`, `tries in a row failed`

### `scripts/check-pii.sh` (or the pre-push hook) lists a hit as "tests/<masked>/<masked>:<masked>:contact <masked>", or in a push as "added line <masked>: …": no file or line to look at
- **Check:** `bash scripts/check-pii.sh` in the dev checkout: a current copy prints each hit as `path:line:` and then the masked content (`docs/limits.md:31:contact <masked>`; in a push, the new file's path and line).
- **Cause:** the masking meant for the matched text ran over the whole hit line, so every part of the path with a digit in it, the line number, and in a push the count of added lines lost their value too.
- **Fix:** take the current `scripts/check-pii.sh` (`git pull`). The content stays masked, and a denylist hit's content stays hidden; a path part is masked only when it would itself be refused (an account-id shape, an `@`, the hit's own pattern or a denylist entry). With an older copy, search the reported files for the pattern the `!!` line names.
- **Fixed in:** `v0.36.0`
- **Code:** `scripts/check-pii.sh` — `mask_hit`, `hide_loc`, `locate_added`, `id_in_name`

### The publisher's issue "taxjson: vX.Y.Z blocked by the gate" lists `[fingerprint] E` hits on the project's published contact address (`SECURITY.md`, `pyproject.toml`)
- **Check:** the issue on the publisher repo names the files and lines; the address there is the one meant to be public. Is it in `email_allowlist` in `~/.config/pii-scan/private.json`?
- **Cause:** the provider `scripts/private-values` built the fingerprint set from every address in `private.json`, the allowlisted ones included, and a fingerprint hit cannot be excepted. v0.30.2 was blocked this way and never published.
- **Fix:** put the address in `email_allowlist` (it is public there), update, and have the maintainer run `kit values sync taxjson`. A blocked tag is final: prepare and cut the next patch version, with the blocked version's bullets copied into `## Unreleased` (its own section stays as tagged).
- **Fixed in:** —
- **Code:** `scripts/private-values` — `private_json`, `email_allowlist`; `tests/test_private_values.py`

### `scripts/promote.sh`: "fatal: tag 'channels-20261022T190607Z' already exists"
- **Check:** two promotes ran within one second (a stable and a beta move back to back, or the test suite's promote test on a fast runner).
- **Cause:** the fast-lane tag is named for the current second, so the second promote made the same name.
- **Fix:** run the promote again; since this fix the second tag waits for the next second's name.
- **Fixed in:** `v0.31.1`
- **Code:** `scripts/promote.sh` — `fast_lane`, `channels-`
