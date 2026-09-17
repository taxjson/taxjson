# REVIEW-2026-07-ui — antagonistic multi-agent review of UI features & options

2026-07-24. 160 agents: 10 antagonistic finders (GUI widgets, GUI data/dialogs,
CLI options, query filters, rendering, web UI, elections flow, frozen/dispatch
seam, GUI-CLI JSON contract, import-run e2e) + completeness critic + 4
targeted round-2 finders (native-views round-trip, filing cluster, web
pages/forms, estimate/tax pane). Every finding survived a 3-lens adversarial
panel (independent re-repro / intended-behavior+duplicate check / user-impact
skeptic; 2-of-3 to confirm). 67 raw -> 30 kept after dedup -> 28 confirmed
round 1; +18 raw round 2 -> 17 confirmed. **45 CONFIRMED, 3 refuted.**
Known limitations and FUZZ-2026-07 conventions were excluded up front.

Severity: 1 critical, 25 major, 19 minor.

**STATUS 2026-07-25: ALL 45 FINDINGS FIXED.** Fix commits: c9e8fe8
(R1 import silent-drop #1/#3), 9b930d1 (R2 elections #4-#7/#33/#34),
0400d75 (R3 GUI #8-#13/#16), 922c8ee (R4 tainted #2), 06a450f (R5
estimate #24/#25/#41-#43), fcc188f (R6 CLI/rendering
#14/#15/#17/#27/#28/#32/#35/#39/#40/#44), 31e6815 (R7 views
#18-#20/#36/#37), f557252 (R8 filing #21-#23/#38), 1e52490 (R9 web
#26/#29-#31). Regressions in tests/test_review_ui_fixes.py and
tests/test_gui_data.py; suite at 1500 green. The findings below are
the historical record — repro commands describe PRE-FIX behavior.


## CRITICAL

### 1. Imported files with uppercase .CSV/.TT suffixes are accepted, shown as recognized, then silently excluded from the run — wrong tax totals with exit 0

- **Finder**: gui-data-dialogs (also found by: import-run-e2e)  |  **Area**: gui-data-dialogs
- **File**: src/taxjson/gui/data.py
- **Detail**: import_input_files (data.py:185) and the GUI's import/drag-drop filters (app.py:350, 385) match suffixes case-insensitively (`suffix.lower() in IMPORTABLE_SUFFIXES`), and detect_input_broker happily identifies the broker, so 'TRADES.CSV' imports with a green 'imported ... [questrade]' log line and 'press Run' status. But the pipeline consumes inputs via case-sensitive globs: group_inputs uses account_dir.glob('*.csv') (taxjson_run.py:500) and the .tt stage uses glob('*.tt') (taxjson_run.py:775); on POSIX pathlib globbing these never match 'TRADES.CSV'/'START.TT'. The check_config populated-dir warning (taxjson_run.py:443) uses the same lowercase globs, so nothing ever mentions the file. With at least one lowercase CSV present, `taxjson run` exits 0 and `taxjson sum` reports totals missing every trade in the uppercase file (demonstrated: XIU.TO gain ~990 CAD vanished; sum showed only 230.10). Uppercase extensions are exactly what several brokers/Excel produce on export.
- **Expected**: Imported files are processed by the run (or the import normalizes the suffix / the run globs case-insensitively / at minimum a warning that TRADES.CSV will be ignored).
- **Actual**: run printed 'parse questrade: 1 file(s)', exit 0; sum showed 230.10 total (XIU.TO gain missing entirely); no warning anywhere about TRADES.CSV or START.TT. Same for .TT: import returns broker 'tt' but glob('*.tt') sees [].

```
# repro
mkdir -p /tmp/hunter-case/inputs/qt; write taxjson.toml (canada/2025/CAD/source_currencies=[]/[accounts.qt] type="taxable"); create /tmp/hunter-src/TRADES.CSV with a valid Questrade header + XIU.TO buy/sell; then: python -c 'from pathlib import Path; from taxjson.gui import data; print(data.import_input_files(Path("/tmp/hunter-case"),"qt",[Path("/tmp/hunter-src/TRADES.CSV")])); from taxjson.bin.taxjson_run import group_inputs; print(group_inputs(Path("/tmp/hunter-case/inputs/qt")))' -> import returns broker 'questrade' but group_inputs sees {}. Add a lowercase questrade CSV with a ZEB.TO trade, then `python -m taxjson.bin.taxjson_run -C /tmp/hunter-case run --no-input; python -m taxjson.bin.taxjson_run -C /tmp/hunter-case sum`
```


## MAJOR

### 2. `winners` (and `sum`) silently include tainted phantom-basis dispositions — fabricated gains ranked as top winners, totals disagree with form-export/carryover/leaps by the phantom amount

- **Finder**: query-filters  |  **Area**: query-filters / winners, sum, ccd-sum row selection
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: Gains files carry dispositions flagged `tainted` (phantom OPENING_BALANCE cost basis, e.g. truncated buy history). The filing-facing consumers exclude them: form-export skips them with a warning (taxjson_form_export.py:84-88), carryover excludes and warns (taxjson_carryover.py:391), and `leaps`/`leaps-sum` exclude via taxjson_run.py:2182 whose docstring claims taint exclusion 'mirrors every filing-facing consumer'. But cmd_winners' aggregation loop (taxjson_run.py:2395-2409) and `taxjson sum` (summarize_gains) have no tainted check — ccd-sum (2371-2412 region, no `t.get("tainted")` test) shares the gap. A user whose history is truncated (the phantom-short signal KNOWN_ISSUES documents as the designed state) sees a fabricated $1,000 'winner' with $0.00 cost ranked #2 and totals that cannot be reconciled with form-export, with no warning anywhere.
- **Expected**: Query summaries advertising 'engine-allowed amounts' either exclude tainted rows like leaps/form-export/carryover, or at minimum print the same skipped-tainted warning so the 1,000.00 discrepancy vs the filing report is explained.
- **Actual**: winners/sum totals are 4,620.00 vs form-export's 3,620.00 with zero indication; the phantom-basis row is presented as the portfolio's #2 winner.

```
# repro
work/margin_gains.json transactions include {"date":"2025-06-15","symbol":"PHANTOM.TO","qty":10,"proceeds":1000.0,"cost":0.0,"gain":1000.0,"tainted":true}.
$ python -m taxjson.bin.taxjson_run -C /tmp/qf-zoo winners  -> row "PHANTOM.TO 1 1,000.00 0.00 1,000.00" ranked #2; TOTAL REALIZED GAIN: 4,620.00 CAD; no warning. `winners --json` total_gain: 4620.0.
$ python -m taxjson.bin.taxjson_run -C /tmp/qf-zoo sum  -> margin STOCK 1,300.00, TOTAL REALIZED 4,620.00 (includes the 1,000 phantom).
$ python -m taxjson.bin.taxjson_run -C /tmp/qf-zoo form-export  -> "warning: skipped 1 tainted disposition(s) with phantom cost basis..." and Line 13200 total gain 3,620.00.
```

### 3. Filename-hint substring match hijacks broker detection: a valid IB statement named ibkr_statement.csv is parsed as Kraken — 0 transactions, run exits 0 with empty books

- **Finder**: import-run-e2e  |  **Area**: import-run-e2e
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: detect_broker (taxjson_run.py:490-493) checks `if hint in lower` for hints ('coinbase','cb_','kraken','kr_') ANYWHERE in the filename before ever looking at content. 'ibkr' is the standard abbreviation users put in Interactive Brokers export names, and 'ibkr_statement.csv' contains 'kr_', so a structurally unambiguous IB statement (first rows 'Statement,Header,...' / 'BrokerName,Interactive Brokers' — content detection returns 'ib') is routed to the Kraken ledger parser, which emits 0 transactions. The run still prints all stages and 'Done.' and exits 0; gains/holdings/sum are empty. The only signal is one mid-log line: 'warning: ibkr_statement.csv parsed to 0 transactions (405 bytes input, brokerage=kraken)...'. The GUI import dialog (detect_input_broker, same function) also reports 'kraken', corroborating the wrong routing instead of flagging it. Control run with identical content named statement.csv detects 'ib' and produces the expected 1 disposition. The hint should be a prefix match (its own error message says 'Rename to START with cb_, kr_') and/or content detection should win when it succeeds.
- **Expected**: detect_input_broker and the run both identify the file as 'ib' (content detection is unambiguous); 1 realized disposition in margin_gains.json, as the control filename produces.
- **Actual**: control: dialog broker 'ib', rc 0, 1 gains tx. ibkr_statement.csv: dialog broker 'kraken', rc 0, 0 gains tx; stdout contains 'warning: ibkr_statement.csv parsed to 0 transactions (405 bytes input, brokerage=kraken)' but the run ends 'Done. Reports in .../reports/' with exit 0 and completely empty books.

```
# repro
Same harness as above but stage an IB CSV ('Statement,Header,Field Name,Field Value\nStatement,Data,BrokerName,Interactive Brokers\nTrades,Header,DataDiscriminator,Asset Category,Currency,Symbol,Date/Time,Quantity,T. Price,C. Price,Proceeds,Comm/Fee,Basis,Realized P/L,MTM P/L,Code\nTrades,Data,Order,Stocks,CAD,XIU,"2025-02-05, 09:31:00",100,30.00,0,-3000,-1,0,0,0,O\nTrades,Data,Order,Stocks,CAD,XIU,"2025-03-20, 10:15:00",-40,35.00,0,1400,-1,0,0,0,C\n') once as statement.csv (control project) and once as ibkr_statement.csv; data.import_input_files(root,'margin',[src]); subprocess.run(data.run_pipeline_cmd(root)); inspect work/margin_gains.json.
```

### 4. `taxjson harvest --options` is a no-op: the wrapper never forwards the flag, so OCC option positions are always excluded (GUI checkbox breaks the same way)

- **Finder**: query-filters (also found by: gui-json-contract)  |  **Area**: query-filters / harvest --options
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: The `harvest` subcommand defines `--options` (help: 'Include OCC option positions (e.g. LEAPS) ... adds a DTE column', taxjson_run.py:5038) but cmd_harvest (taxjson_run.py:4025-4111) never reads `args.options` and never appends `--options` to the taxjson-harvest child argv (it forwards symbols/--no-ibkr/--ibkr-port/--json/--verbose around line 4099 only). taxjson_harvest.load_positions skips every OCC symbol unless it receives `--options`, so option positions (e.g. LEAPS the user wants to harvest) are silently absent whether or not the flag is given. The GUI routes through the same wrapper: gui/data.py harvest_cmd() appends '--options' to a `taxjson harvest` command (data.py:82-91), so the desktop app's include-options toggle is also inert.
- **Expected**: `taxjson harvest --options` includes OCC option positions (priced via IBKR/cache, DTE column), same as the standalone tool with --options.
- **Actual**: The wrapper reports no positions; the flag parses successfully but changes nothing. Identical inputs through the standalone with --options show the option position with unrealized gain 250.00.

```
# repro
Zoo project /tmp/qf-zoo (taxjson.toml canada/2025/CAD; work/margin_gains.json inventory contains open option {"symbol":"OPT260918C00015000.TO","qty":1,"total_cost":500}; work/.price_cache.json primed with {"OPT260918C00015000.TO":{"price":7.5,"asof":"2026-07-24","source":"ibkr"}}).
$ python -m taxjson.bin.taxjson_run -C /tmp/qf-zoo harvest OPT260918C00015000.TO --options --no-ibkr
-> "No open positions for OPT260918C00015000.TO." (rc=0)
$ python -m taxjson.bin.taxjson_harvest /tmp/qf-zoo/work/margin_gains.json --options --no-ibkr --symbol OPT260918C00015000.TO --price-cache /tmp/qf-zoo/work/.price_cache.json
-> row "margin OPT260918C00015000.TO 1 5.0000 7.5000? 250.00 50.0% GAIN 56d ..." with DTE column and TOTAL 250.00.
```

### 5. elect --redo without a TTY wipes the saved elections, then crashes with a raw traceback

- **Finder**: elections-flow  |  **Area**: elections-flow
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: cmd_elect --redo clears the election(s) from the manifest and saves it BEFORE attempting the interactive re-prompt (clear at ~1628-1638, re-prompt at :1656 run_to_file(cmd, out, interactive=True)). The redo subprocess is always launched interactively — no --no-input/--pending-json fallback like stage_account has — so when stdin is not a TTY (script, cron, GUI wrapper, or `< /dev/null`), taxjson-corp-actions exits 3, run_to_file raises, and the user gets an unhandled CalledProcessError traceback (exit 1). Their previously saved election is already gone: the manifest — the one artifact documented as a non-rebuildable user decision — is left as {"elections": {}} with nothing re-recorded. Same wipe-first ordering means a Ctrl-C at the prompt also loses the decisions.
- **Expected**: Either refuse the redo up front when stdin is not a TTY (before clearing), or clear only after a replacement election is captured; clean error message, no traceback.
- **Actual**: Output ends with subprocess.CalledProcessError traceback (exit 1) after 'taxjson-corp-actions: error: ... stdin is not a TTY'; manifest afterwards is {"elections": {}} — the ignore election the user had saved is destroyed with no replacement.

```
# repro
mkdir -p /tmp/p/inputs/margin; write taxjson.toml (canada/CAD, [accounts.margin] taxable) and the SSL->RGLD merger CSV from tests/test_pending_elections.py into inputs/margin/; python -m taxjson.bin.taxjson_run -C /tmp/p elect margin --set '75f5b42990df=ignore'; python -m taxjson.bin.taxjson_run -C /tmp/p elect margin --redo < /dev/null; cat /tmp/p/inputs/margin/manifest.json
```

### 6. Non-numeric hint value accepted by elect --set (and the GUI hint field) then crashes the next run with a raw ValueError traceback

- **Finder**: elections-flow  |  **Area**: elections-flow
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: cmd_elect --hint parsing (taxjson_run.py:1596-1599) tries float(v) and on ValueError silently stores the raw string in the manifest. Nothing downstream can consume a string hint: corp_actions.py:981 does float(hints.get('fmv_per_share') or 0.0) with no guard. So `elect --set ... --hint fmv_per_share=12,50` (comma decimal — a normal European typo) reports 'Election saved' with exit 0, and the NEXT `taxjson run --no-input` dies with 'ValueError: could not convert string to float: 12,50' plus a CalledProcessError traceback, exit 1. The GUI is worse: gui/elections.py hint QLineEdit is free text with no validation, gui/data.py set_election forwards it verbatim (verified offscreen: typing '  12,50 ' yields hints {'fmv_per_share': '12,50'}), so a GUI user who mistypes a hint gets 'Run failed (exit 1) — see Log' with a Python traceback and no dialog ever re-offers the hint — they are stuck unless they know to hand-run `elect --set` again.
- **Expected**: Reject a non-numeric value for a numeric hint at --set time (exit non-zero with a message), or validate in the dialog; the run should never traceback on a manifest the tool itself wrote.
- **Actual**: elect exits 0 and writes "fmv_per_share": "12,50" to inputs/margin/manifest.json; `run --no-input` exits 1 with raw ValueError + CalledProcessError tracebacks (corp_actions.py:981).

```
# repro
python -m taxjson.bin.taxjson_run -C /tmp/p elect margin --set '75f5b42990df=taxable_disposition' --hint 'fmv_per_share=12,50'   # exit 0, 'Election saved'; then python -m taxjson.bin.taxjson_run -C /tmp/p run --no-input   # exit 1. GUI side: build ElectionsDialog offscreen (QT_QPA_PLATFORM=offscreen) from the real pending doc, setText('12,50') on the hint edit, choices() returns the string.
```

### 7. elect --set validates the election against the whole country, not the event's type — wrong-type election saved OK, next run crashes

- **Finder**: elections-flow  |  **Area**: elections-flow
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: cmd_elect --set builds `known` from every rule's options for the country (taxjson_run.py:1584-1590), so a spinoff-only election is accepted for a merger event: `elect margin --set 75f5b42990df=rollover_s_86_1` prints 'Election saved', exit 0. The per-event check only happens at apply time (corp_actions.py:1674-1677 raises KeyError), so the next `taxjson run --no-input` fails with a raw KeyError traceback wrapped in a CalledProcessError traceback, exit 1 — not the exit-3 pending path and not a clean error. When the event IS in the pending doc (the normal case here — it was listed by the exit-3 run), elect has everything it needs to validate per-event and refuses nothing.
- **Expected**: elect --set rejects an election that is not one of the pending event's options (the pending doc lists them), or at minimum the run surfaces a clean 'taxjson-corp-actions: error:' and re-pends the event instead of a Python traceback.
- **Actual**: elect exits 0; the subsequent run exits 1 with KeyError + CalledProcessError tracebacks.

```
# repro
python -m taxjson.bin.taxjson_run -C /tmp/p elect margin --set '75f5b42990df=rollover_s_86_1'   # exit 0 'Election saved'; python -m taxjson.bin.taxjson_run -C /tmp/p run --no-input   # exit 1, KeyError: "unknown election 'rollover_s_86_1' for canada/merger"
```

### 8. elect --set (and --hint) silently ignored when the account argument is omitted — exit 0, nothing saved

- **Finder**: elections-flow (also found by: cli-options)  |  **Area**: elections-flow
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: In cmd_elect the `--set` branch is only reached after the account check; the no-account branch (taxjson_run.py:1559-1568) only guards --redo/--reset, then prints the read-only listing and returns. So `taxjson elect --set EVENT=ELECTION` (account forgotten) prints the elections listing, exits 0, and saves nothing — no warning that --set was ignored. A CI/bootstrap script (the flag's stated audience) sees success and the next run still exits 3; the GUI's `taxjson elect <acct> --set` always passes the account so a human hand-typing the documented one-liner is the victim.
- **Expected**: Error: '--set needs an account' (like the existing --redo/--reset guard two lines up), non-zero exit.
- **Actual**: Exit 0, account listing printed, election not written.

```
# repro
cd /tmp/p; python -m taxjson.bin.taxjson_run -C . elect --set 75f5b42990df=ignore; echo $?   # prints 'Corporate-action elections: ... margin: no elections recorded.' and 0; manifest unchanged
```

### 9. refresh_tables discards CLI stderr: corrupt gains file renders an all-zero summary with no warning anywhere

- **Finder**: gui-widgets (also found by: frozen-seam)  |  **Area**: gui-widgets
- **File**: src/taxjson/gui/app.py
- **Detail**: `taxjson sum` warns on stderr ("taxjson: warning: could not read .../margin_gains.json: Expecting property name...") and exits 0 with empty accounts when a work/*_gains.json is unreadable. The GUI's refresh_tables (line 398: `doc, err = data.load_summary(...)`) never surfaces `err` on the success path (and line 411 explicitly discards positions errors into `_err`). Opening such a project shows a summary of TOTAL 0.00, status "tax year None, basis: pre-wash", and a completely empty Log — the CLI user sees the warning, the GUI user sees silent zeros. A user whose gains file was truncated by a crashed run could read 0.00 realized gains as truth.
- **Expected**: The stderr warning appears in the Log tab (and ideally the status bar flags the unreadable report) instead of silently rendering zeros
- **Actual**: status = '<root> — tax year None, basis: pre-wash', summary table shows TOTAL 0.00, win.log.toPlainText() == '' — the warning is dropped

```
# repro
mkdir -p corrupt/work; write taxjson.toml; echo '{corrupt json !!' > corrupt/work/margin_gains.json; `python -m taxjson.bin.taxjson_run -C corrupt sum` prints the warning on stderr, rc=0. Then QT_QPA_PLATFORM=offscreen python .../scratchpad/probe4.py (section S): _create_window(corrupt_root), inspect status and log
```

### 10. Switching to a project without reports leaves the previous project's summary/positions rows on screen

- **Finder**: gui-widgets (also found by: frozen-seam, gui-json-contract)  |  **Area**: gui-widgets
- **File**: src/taxjson/gui/app.py
- **Detail**: refresh_tables (lines 397-416) only calls summary_table.load(...) when load_summary returns a doc, and positions_table.load(...) when load_positions returns rows; neither table is cleared on the None path. Open project A (has work/*_gains.json), then open project B (fresh project, no reports): the window title and status say B ("no reports yet: press Run"), but both tables still display A's account rows, gains, and open positions. A user comparing two tax projects sees project A's realized-gains numbers presented under project B's title.
- **Expected**: Opening a project with no reports clears the Summary and Positions tables (or shows an explicit empty state) so no data from the previously open project remains
- **Actual**: After open_project(B): title 'taxjson — B', status '.../B — no reports yet: press Run ▶', but summary_table still has 2 rows ('margin', 'TOTAL') and positions_table still shows 'AAA.TO' — all from project A

```
# repro
source venv/bin/activate && QT_QPA_PLATFORM=offscreen python <scratch>/probe1.py  (section D: open_project(A) then open_project(B))
```

### 11. Pipeline finishing after a project switch acts on the wrong project — including opening the elections dialog for the new project's stale pending file

- **Finder**: gui-widgets  |  **Area**: gui-widgets
- **File**: src/taxjson/gui/app.py
- **Detail**: run_pipeline captures no project context; _run_finished (lines 443-456) and _handle_elections (line 459) use self.project at finish time. If the user runs project A and opens project B before the run finishes: (a) a failing run shows 'Run failed (exit 2) — see Log.' while the window shows project B, which the user never ran; (b) an rc-3 (elections required) run calls load_pending_elections(B) — if B has a stale work/pending_elections.json from an earlier run, the GUI opens the ElectionsDialog for B's old events (demonstrated: event 'stale-b-evt' from B was offered after A's run exited 3), and accepting would run set_election against B and re-run the pipeline on B. A's actual pending elections are never surfaced, and B can get elections/runs the user never intended.
- **Expected**: A run started for project A reports and applies its results (including the elections flow) to project A only, or is cancelled/ignored when the project changes
- **Actual**: exit-2 variant: title 'taxjson — B' with status 'Run failed (exit 2) — see Log.'; exit-3 variant: ElectionsDialog opened listing B's stale event ['stale-b-evt'], status 'Elections postponed — run again when ready.' — all attributed to B

```
# repro
source venv/bin/activate && python -u <scratch>/probe6.py  (pipeline cmd stubbed to sleep-then-exit 2 / exit 3; open_project(B) mid-run; spy ElectionsDialog records offered event_ids)
```

### 12. list_accounts raises SystemExit when taxjson.toml is missing — kills the whole GUI process on drag-drop; also raises AttributeError on a plausible malformed accounts table

- **Finder**: gui-data-dialogs (also found by: frozen-seam)  |  **Area**: gui-data-dialogs
- **File**: src/taxjson/gui/data.py
- **Detail**: list_accounts (data.py:139-146) wraps load_config in `except Exception`, but load_config calls sys.exit(...) when taxjson.toml is absent (taxjson_run.py:360) — SystemExit is a BaseException, so it escapes despite the documented ''return []'' contract. In the GUI, if the open project's taxjson.toml disappears (renamed by an editor, sync conflict, unmounted volume), the next Import Files/drag-drop calls list_accounts inside a Qt slot; PySide6's error handling turns the escaping SystemExit into process termination — the entire GUI exits with code 1 and no dialog. Separately, the account-iteration loop (data.py:148-151) is outside the try: with the plausible hand-edit `[accounts]\nmargin = "taxable"` (plain key instead of a table), `(acfg or {}).get` raises AttributeError ('str' object has no attribute 'get') out of list_accounts, breaking the import slot with a traceback instead of the intended 'No accounts' warning.
- **Expected**: list_accounts returns [] on any config problem (its docstring/callers assume this; app.py then shows the 'No accounts' message box).
- **Actual**: SystemExit propagates through the Qt event loop and terminates the whole GUI (observed PROCESS EXIT: 1 with only the CLI error text on stderr); malformed accounts table raises AttributeError out of the slot.

```
# repro
python -c 'from pathlib import Path; from taxjson.gui import data; data.list_accounts(Path("/tmp/hunter-notaproject"))' -> raises SystemExit('taxjson: no taxjson.toml ...'). GUI-level: QT_QPA_PLATFORM=offscreen script that opens _create_window on a valid project, unlinks taxjson.toml, then QTimer-invokes win.import_files([csv]) inside app.exec() -> process exits 1; 'slot survived'/'loop exited' never print. AttributeError: write taxjson.toml with '[accounts]\nmargin = "taxable"' and call data.list_accounts(root) -> AttributeError raised.
```

### 13. import_input_files has no per-file error handling — an unreadable file, folder, or broken symlink mid-batch aborts the import, leaves partial copies, and the GUI shows nothing

- **Finder**: gui-data-dialogs (also found by: gui-widgets)  |  **Area**: gui-data-dialogs
- **File**: src/taxjson/gui/data.py
- **Detail**: shutil.copy2 at data.py:192 is uncaught, and app.py:366 calls import_input_files with no try/except. A batch [good1, unreadable, good2] raises PermissionError after good1 was already copied: the results list (and thus all 'imported ...' log lines and the status update) is lost, good2 is never copied, and the exception escapes the Qt slot — the user gets no dialog, the log stays empty, yet good1 now silently sits in inputs/<acct>/ and will be picked up by the next run (state corruption after error; the user will likely re-import, and only dedup luck prevents double data). Same crash for a drag-dropped folder named e.g. 'folder.tt' (detect_input_broker returns 'tt' for a DIRECTORY without an is_file check, dialog shows it as a recognized broker, copy2 then raises IsADirectoryError) and for a broken symlink (FileNotFoundError).
- **Expected**: Per-file error capture (e.g. a {source, error} entry in results) so the dialog/log can report which files failed and the copies that succeeded are acknowledged; directories rejected at detection time.
- **Actual**: First failing file aborts the whole batch with an exception escaping the slot; earlier files are silently in the project, later files silently dropped, no log line, no status change, no error dialog.

```
# repro
python: create a_good.csv, b_locked.csv (chmod 0), c_good.csv; data.import_input_files(root,'qt',[a,b,c]) -> raises PermissionError, inputs/qt contains only a_good.csv. GUI: QT_QPA_PLATFORM=offscreen, monkeypatch ImportDialog.exec to auto-accept, win.import_files([g1, locked, g2]) -> traceback 'PermissionError ... data.py:192 shutil.copy2', win.log empty, status unchanged, only g1 copied. Also data.detect_input_broker(Path('/tmp/hunter-src/folder.tt')) -> 'tt' for a directory; import of it raises IsADirectoryError; broken symlink link.csv raises FileNotFoundError.
```

### 14. Wizard-created project + Run with no imports crashes with a Python traceback (fees stage, exit 2 'no input files') — unfixed variant of FUZZ-2026-07 #12

- **Finder**: gui-data-dialogs (also found by: import-run-e2e)  |  **Area**: gui-data-dialogs
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: The exact GUI flow — data.create_project(...) then the Run button (data.run_pipeline_cmd -> `taxjson run --no-input`) — on a project with no inputs yet prints per-account 'no CSVs ... skipping' warnings, then stage_fees (taxjson_run.py:1171) runs taxjson-fees with an empty cache; taxjson_fees.py:394 argparse-errors 'no input files (pass parsed per-broker JSONs or --cache DIR)' with exit 2, and run_to_file raises an unhandled subprocess.CalledProcessError -> full traceback, run exits 1. FUZZ-2026-07 #12 fixed the 'No trading fees found' exit-1 path (taxjson_fees.py:422-426 now returns 0) but this zero-parsed-accounts exit-2 path still crashes. In the GUI this is the very first Run a new user is invited to press ('...then Run'), and it is also exactly what happens after finding-1 (only uppercase-suffix files imported). Not listed in AUDIT-2026-07-*.md (no 'no CSVs' hits).
- **Expected**: Clean message ('no inputs in any account — import broker CSVs first') and a non-traceback failure, or exit 0 with empty reports, matching the FUZZ #12 fix's intent.
- **Actual**: Traceback ending 'subprocess.CalledProcessError: ... taxjson.bin.taxjson_fees ... returned non-zero exit status 2.', RC=1; the GUI log pane shows the raw traceback.

```
# repro
python -c 'from pathlib import Path; from taxjson.gui import data; print(data.create_project(Path("/tmp/hunter-fresh"), "canada", 2025))' (rc 0), then: python -m taxjson.bin.taxjson_run -C /tmp/hunter-fresh run --no-input; echo RC=$?
```

### 15. taxjson fees-sum --json crashes (KeyError 'base', exit 1) whenever no fee rows are found

- **Finder**: rendering  |  **Area**: fees renderer (taxjson_fees.py render_json / main no-data path)
- **File**: src/taxjson/bin/taxjson_fees.py
- **Detail**: In main(), the no-data branch calls render_json({}, {"total": 0.0}, info, ...) (line 416), but render_json's serialize() does metrics(bucket["base"]) and bucket["cur"] (lines 328-331), so the placeholder grand dict raises KeyError: 'base'. Text mode correctly prints 'No trading fees found' with exit 0 (the FUZZ-2026-07 #H convention explicitly says no-data must be SUCCESS), but --json emits a traceback, no JSON on stdout, and exit 1. Any commission-free broker (e.g. Wealthsimple) plus --json hits this — both via the standalone tool and the `taxjson fees-sum --json` wrapper, so machine consumers (GUI/scripts) break on a perfectly normal project.
- **Expected**: --json prints a valid JSON document with zeroed totals/empty brokerages and exits 0, mirroring the text mode's 'No trading fees found' success convention.
- **Actual**: Traceback ending 'File .../taxjson_fees.py, line 329, in serialize d = {**metrics(bucket["base"]), ... KeyError: 'base'', exit code 1, nothing on stdout.

```
# repro
mkdir -p /tmp/rend-p3/work; write taxjson.toml ([settings] year=2026 country=canada base_currency=CAD source_currencies=[]; [accounts.margin] type=taxable); write /tmp/rend-p3/work/margin_wealthsimple.json = {"metadata":{"source_brokerage":"wealthsimple"},"transactions":[{"id":"t1","action":"BUYSELL","date":"2026-01-05","symbol":"XEQT.TO","quantity":10,"currency":"CAD","net_amount":-300.0,"gross_amount":-300.0,"fee":0.0,"commission":0.0}]}; then: python -m taxjson.bin.taxjson_run -C /tmp/rend-p3 fees-sum (ok, exit 0) vs python -m taxjson.bin.taxjson_run -C /tmp/rend-p3 fees-sum --json (traceback, exit 1). Also directly: python -m taxjson.bin.taxjson_fees /tmp/rend-p3/work/margin_wealthsimple.json --json
```

### 16. align_columns tables silently corrupt rows when an account name (or any cell) contains a space — numbers land under the wrong headers

- **Finder**: rendering  |  **Area**: report_model.align_columns + all taxjson_run table views (sum, list, events, ...)
- **File**: src/taxjson/lib/report_model.py
- **Detail**: align_columns (line 47: rows = [ln.split() for ln in lines]) tokenizes on whitespace, and every table builder in taxjson_run.py joins cells with " " (e.g. cmd_summary line 3219, cmd_positions line 3647, _run_tx_view line 1969). An account named with a space — legal TOML: [accounts."rrsp x"], and resolve_gains_files happily returns it from work/'rrsp x_gains.json' — splits into two tokens, shifting every subsequent column of that row AND widening all columns. In `taxjson sum` the 'rrsp x' row shows STOCK=x, OPTION=500.00, REALIZED=0.00, DIVIDEND=500.00 ... TOTAL column shows the wrong figure; in `taxjson list` SYMBOL=x, QTY=XIU.TO, COST=50; `taxjson events` likewise. --json output is correct ('account': 'rrsp x'), so text and JSON disagree on the same command.
- **Expected**: Each cell occupies exactly one column regardless of embedded spaces (quote/escape the cell, or use a structured renderer), so 'rrsp x' stays in ACCOUNT and 500.00 lands under STOCK/REALIZED.
- **Actual**: taxjson sum row: 'rrsp      x               500.00   0.00            500.00     0.00   0.00   0.00            500.00' — every value is under the wrong header (OPTION shows 500.00, REALIZED shows 0.00, TOTAL column shows dividend slot); taxjson list row: 'rrsp   x   XIU.TO   50   1,500.00   30.00   -   2026-01-02' shifted one column right.

```
# repro
Project /tmp/rend-p1 (taxjson.toml with [accounts."rrsp x"] type=sheltered) with crafted work/'rrsp x_gains.json' (one SELL gain 500, inventory XIU.TO qty 50 cost 1500) plus a normal work/margin_gains.json. Run: python -m taxjson.bin.taxjson_run -C /tmp/rend-p1 sum ; ... list ; ... events. Compare with `sum --json` / `list --json` which show account 'rrsp x' with correct fields.
```

### 17. JsonTable numeric cells: setData(EditRole) clobbers the formatted text — values >= 1,000 lose money formatting, non-2dp floats render at full double precision, and column sorting is wrong

- **Finder**: gui-json-contract (also found by: gui-widgets)  |  **Area**: gui-tables (Summary/Positions/Harvest)
- **File**: src/taxjson/gui/app.py
- **Detail**: JsonTable.load (app.py lines 104-120) creates the item with text f'{v:,.2f}' then calls item.setData(Qt.ItemDataRole.EditRole, v) at line 114. For QTableWidgetItem, EditRole IS DisplayRole, so the double replaces the formatted string whenever QVariant('3,000.00') != QVariant(3000.0) (the comma makes the string non-convertible, and any float that isn't exactly 2-decimal compares unequal). Consequences on real CLI --json output: (a) every amount >= 1,000 displays as raw '3000'/'1200' with no thousands separator or decimals while neighbours show '-500.00'; (b) harvest rows (emitted unrounded by taxjson-harvest) display as '157.86000061035156', '3093.9998626708984', '-215.72000122070312'; (c) cells in one column are stored as a mix of strings (<1,000) and doubles (>=1,000), so clicking a header sorts by string comparison across types — COST ascending gave -500.00, 1200, 3000, 400.00 (400 sorted after 3000). Every money/qty column in Summary, Positions and Harvest is affected for any realistic portfolio.
- **Expected**: All numeric cells display as '3,000.00' / '157.86' (the f'{v:,.2f}' the code computes) and numeric columns sort by value.
- **Actual**: Cells >= 1,000 show unformatted raw doubles, unrounded CLI floats show 13+ digits, and numeric sort interleaves string- and double-stored cells in the wrong order.

```
# repro
QT_QPA_PLATFORM=offscreen python gui_drive.py (instantiates taxjson.gui.app.MainWindow on /tmp/f5gui-ca; refresh_tables + _render_harvest on real `list --json` / `harvest --json` output): positions COST cell = '3000', harvest PRICE_BASE = '157.86000061035156', UNREALIZED = '-1078.6000061035156'. Sorting: probe_sort.py calls positions_table.sortItems(3, AscendingOrder) -> order SHOP.TO -500.00, BNS.TO 1200, BNS.TO 3000, option 400.00. Minimal probe (probe_table.py): QTableWidgetItem('3,000.00'); setData(EditRole, 3000.0); item.text() -> '3000'; for 157.86000061035156 text -> '157.86000061035156'.
```

### 18. taxjson serve --host 0.0.0.0 yields a server that refuses every network request with 400

- **Finder**: web-ui  |  **Area**: web-ui
- **File**: src/taxjson/web/server.py
- **Detail**: server.py:22 passes allowed_hosts=[host] to create_app. When the user binds 0.0.0.0 (the documented way to expose the UI — server.py even prints a 'binding to 0.0.0.0 exposes your tax data on the network' warning implying it works), the TrustedHostMiddleware allowlist becomes {127.0.0.1, localhost, ::1, testserver, 0.0.0.0}. No real client ever sends Host: 0.0.0.0 — a LAN browser hitting http://192.168.1.5:8765 sends Host: 192.168.1.5:8765 and gets 400 'Invalid host header' on every route. The flag silently does the wrong thing: the server binds all interfaces but serves nobody except 127.0.0.1. Same for --host ::. Binding a specific IP (--host 192.168.1.5) works, confirming the wildcard-host case is the hole.
- **Expected**: Binding 0.0.0.0/:: should accept requests addressed to any of the machine's names/IPs (e.g. map wildcard binds to allowed_hosts=['*'] or the machine's addresses), or serve should refuse the combination with a clear error
- **Actual**: Every request whose Host header is not literally a loopback name is refused with 400; the exposed server is unusable from the network it was deliberately exposed to

```
# repro
cd scratchpad; python attack6.py — builds app exactly as serve() does: create_app(ctx, allowed_hosts=["0.0.0.0"]); TestClient GET / with headers={'host':'192.168.1.5:8765'} -> 400 'Invalid host header'; host 'mymac.local:8765' -> 400; control create_app(ctx, allowed_hosts=['192.168.1.5']) same request -> 200
```

### 19. The 'round-trippable' taxtext printed by `trades`/`events <account>` drops all fees: re-importing it turns TOTAL FEES 59.94 into 'No fees incurred'

- **Finder**: r2:native-views-roundtrip  |  **Area**: native-views-roundtrip
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: _run_tx_view's comment (~line 1953) promises 'A single named account prints pure taxtext (round-trippable)', but the lines are produced by _tx_display_line, the DISPLAY formatter, not tx_to_tt_line. For a Questrade account every BUYSELL line ends in fee 0.00 (see finding 1), so saving the view and re-importing it as a .tt input permanently erases every commission: parse_tt_line (taxjson_convert_tt.py:63) reads that 0.00 as the fee. After round-trip, `taxjson fees` reports 'No fees incurred in tax year 2025' and `taxjson sum` shows FEES 0.00 instead of 59.94 — the deductible-expense figure is silently destroyed by the tool's own documented round-trip.
- **Expected**: Round-tripped project reports the same fees as the original: fees.rpt with 6 rows totalling 59.94 CAD; sum FEES column 59.94.
- **Actual**: Original project: '6 fee(s); TOTAL FEES: 59.94 CAD', sum FEES 59.94. Round-tripped project: 'No fees incurred in tax year 2025.', sum FEES 0.00 (REALIZED/DIVIDEND columns unchanged, so the loss is silent).

```
# repro
python -m taxjson.bin.taxjson_run -C /tmp/nvr2-nv events margin > /tmp/events_margin.tt; mkdir -p /tmp/nvr2-rt/inputs/margin; cp /tmp/events_margin.tt /tmp/nvr2-rt/inputs/margin/fix.tt; add taxjson.toml (same settings, margin only) and a 1-row zero-commission questrade_extra.csv (needed because a .tt-only account is skipped — see separate finding); python -m taxjson.bin.taxjson_run -C /tmp/nvr2-rt run --no-input; python -m taxjson.bin.taxjson_run -C /tmp/nvr2-rt fees; python -m taxjson.bin.taxjson_run -C /tmp/nvr2-rt sum
```

### 20. sig() in the 'round-trippable' view rounds quantities to 8 decimals, changing transaction ids — re-importing the export next to the original defeats dedup and doubles realized gains

- **Finder**: r2:native-views-roundtrip  |  **Area**: native-views-roundtrip
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: _tx_display_line's sig() (~line 1770) formats qty/price with ':.8f', while its own docstring says a crypto qty 'must not be rounded away' and _run_tx_view claims the single-account view is round-trippable. A 9-decimal qty 0.123456789 prints as 0.12345679; parse_tt_line then computes the id from repr(float(qty)) (taxjson_convert_tt.py:120-140), so the round-tripped row gets id 67720e4cd5af52e4 vs the original a5c5664a0d633775. Consequence demonstrated: a user who re-imports the exported view alongside their original .tt gets NO dedup — every trade is booked twice and realized gain doubles from 2,469.13 to 4,938.26 CAD, and position quantities drift (0.123456789 -> 0.12345679). Control: importing the byte-identical original .tt twice correctly dedups to 2,469.13, proving the rounding-induced id drift is what breaks it.
- **Expected**: The single-account view is round-trippable as documented: full-precision quantities, stable ids, and re-importing the export next to the original dedups to the same totals (2,469.13 CAD).
- **Actual**: Export shows qty 0.12345679 (9th digit lost); convert-tt ids differ (orig a5c5664a0d633775 / roundtrip 67720e4cd5af52e4); project with original + re-imported export reports REALIZED 4,938.26 CAD — exactly double the true 2,469.13 — with exit 0.

```
# repro
mkdir -p /tmp/nvr2-cr/inputs/margin; manual.tt = 'BUYSELL 2025-01-06 09:30:00 BTC 0.123456789 CAD 100000 12345.68 0.00' + matching -0.123456789 sell 14814.81; plus a 1-row zero-commission CSV; run; `events margin` prints qty 0.12345679; python -m taxjson.bin.taxjson_run -C /tmp/nvr2-cr events margin > /tmp/cr_export.tt; taxjson-convert-tt on both files shows ids a5c5664a0d633775 vs 67720e4cd5af52e4. Then /tmp/nvr2-dd with manual.tt + the exported BUYSELL lines as reimported.tt: sum -> 4,938.26. Control /tmp/nvr2-dd2 with manual.tt copied twice verbatim: sum -> 2,469.13.
```

### 21. An account whose inputs folder contains only .tt files is skipped with a misleading 'no CSVs' warning — its entire book (a $15,000 gain) vanishes from all reports with exit 0

- **Finder**: r2:native-views-roundtrip  |  **Area**: native-views-roundtrip
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: .tt files are a documented input (README '*.tt # optional starting-position files'; run.py:736 even instructs users to 'record them manually via a .tt file') and are converted at stage 3 (line 775, acct_dir.glob('*.tt')). But the account gate at lines 673-676 checks only group_inputs() — which groups CSVs — and returns None ('no CSVs in ...; skipping') before stage 3 ever runs. The config lint at line 443 explicitly counts .tt as data ('any(sub.glob("*.csv")) or any(sub.glob("*.tt"))'), so the tool is internally inconsistent. A hand-maintained account (e.g. manual crypto records, exactly the .tt use case) is silently dropped: run exits 0, sum/list/gains omit the account entirely; in a single-account project the run instead dies in the fees stage with a raw CalledProcessError traceback.
- **Expected**: The manual account's .tt is converted (as it is when a CSV coexists in the same folder — verified in /tmp/nvr2-rt where 'convert-tt fix.tt' runs) and sum shows a manual row with REALIZED 15,000.00; or at minimum the warning names .tt files as unsupported alone.
- **Actual**: 'taxjson: warning: no CSVs in /private/tmp/nvr2-tt/inputs/manual; skipping account 'manual'.' — RUN_RC=0, and `sum` shows only margin (TOTAL 22.53); the 15,000.00 CAD realized gain is absent from every report.

```
# repro
mkdir -p /tmp/nvr2-tt/inputs/{margin,manual}; taxjson.toml with accounts.margin (Questrade CSV) and accounts.manual (taxable); inputs/manual/manual.tt = BUYSELL BTC 0.5 @100000 buy + 0.5 @130000 sell (true gain 15,000.00 CAD); python -m taxjson.bin.taxjson_run -C /tmp/nvr2-tt run --no-input; echo $?; python -m taxjson.bin.taxjson_run -C /tmp/nvr2-tt sum
```

### 22. reconcile-slips silently drops slip rows with unparseable proceeds and certifies a disagreeing slip as fully reconciled (exit 0)

- **Finder**: r2:filing-cluster  |  **Area**: filing-cluster/reconcile-slips
- **File**: src/taxjson/bin/taxjson_reconcile_slips.py
- **Detail**: load_slip (lines 115-117) does `if proceeds is None: continue` — any slip row whose proceeds cell is 'N/A', blank, an em-dash, or a French-locale number like "7 600,00" (_clean_amount strips commas then fails on the space) is dropped with NO warning. The whole point of the tool is 'finds every divergence... CRA machine-matches returns against these slips', yet a T5008 that lists an extra 100-share BCE disposition (qty 100, cost 3700, proceeds box unreadable/N-A) reconciles as '2 OK, 0 mismatch' with exit 0 — the documented 'everything reconciled' code — while the slip disagrees with the computed book by 100 shares and $3,700 of cost. The French-locale variant drops EVERY row and then reports each computed symbol as 'MISSING_FROM_SLIP ... no slip row', which is factually false (the slip has the rows) and points the user at the wrong diagnosis.
- **Expected**: The dropped row should be reported (e.g. a per-row 'unparseable proceeds' warning and a non-clean status), and the run should exit 1 — the slip's 300 BCE shares do not match the computed 200.
- **Actual**: Output: 'BCE  OK / XIU  OK ... 2 OK, 0 mismatch, 0 missing from computed, 0 missing from slip', EXIT=0. No warning anywhere that a slip row was ignored. French-locale slip: every symbol reported 'MISSING_FROM_SLIP — no slip row (missing slip...?)' though the slip contains all rows.

```
# repro
Project /tmp/fc2-base (canada/2025/CAD, Questrade CSV with BCE.TO wash-sale + XIU.TO gain; `taxjson run --no-input` done). Then:
printf 'Symbol,Quantity,Proceeds of disposition,Cost or other basis\nBCE.TO,200,7600.00,9004.99\nBCE.TO,100,N/A,3700.00\nXIU.TO,100,3890.00,3204.99\n' > slip_na2.csv
python -m taxjson.bin.taxjson_run -C /tmp/fc2-base reconcile-slips slip_na2.csv; echo EXIT=$?
French variant: slip with "7 600,00"/"9 004,99" quoted cells → all rows dropped.
```

### 23. reconcile-slips crashes with a raw UnicodeDecodeError traceback on Windows-1252/Latin-1 T5008 slip CSVs (French broker exports)

- **Finder**: r2:filing-cluster  |  **Area**: filing-cluster/reconcile-slips
- **File**: src/taxjson/bin/taxjson_reconcile_slips.py
- **Detail**: load_slip line 98 opens the slip with encoding='utf-8-sig' only. Québec-broker T5008 exports (Desjardins, NBDB, RBC French) are commonly cp1252/latin-1 with accented descriptions (é = 0xE9, em-dash = 0x97). The first non-ASCII byte raises UnicodeDecodeError as an uncaught traceback with exit 1 — not the documented exit 2 usage error, no message telling the user it is an encoding problem or how to fix it. The header docs advertise 'export it from your broker ... common broker spellings work as-is'.
- **Expected**: Either decode with a latin-1/cp1252 fallback (the amounts/symbols are ASCII; only descriptions carry accents) or exit 2 with a clear 'slip is not UTF-8 — re-export as UTF-8 or convert with iconv' message.
- **Actual**: Traceback ending "UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97 in position 87: invalid start byte", EXIT=1.

```
# repro
python3 -c "open('/tmp/fc2-base/slip_cp1252.csv','wb').write('Symbol,Description,Quantity,Proceeds of disposition,Cost or other basis\nBCE.TO,BCE Inc — Montréal Québec,200,7600.00,9004.99\n'.encode('cp1252'))"
python -m taxjson.bin.taxjson_run -C /tmp/fc2-base reconcile-slips /tmp/fc2-base/slip_cp1252.csv; echo EXIT=$?
```

### 24. carryover --claimed silently ignores claims recorded for any year that has no dispositions in the book — carryforward balance overstated with no warning

- **Finder**: r2:filing-cluster  |  **Area**: filing-cluster/carryover
- **File**: src/taxjson/bin/taxjson_carryover.py
- **Detail**: build_canada_ledger iterates `for y in years` where years = sorted(nets) — only years containing dispositions — and folds claims via `claimed.get(y)` (line 128). A claim recorded for a year outside that set is never added to pending_claim, never reduces the balance, and never appears in unmatched_claims. This is the tool's PRIMARY use case gone wrong: you apply a 2025 net capital loss on a LATER year's return (e.g. 2026, when you sold nothing, or against T3 capital-gains distributions that aren't in the broker book), record '2026 20.00' exactly as the doc says ('the amount claimed on that YEAR's return'), and the ledger silently keeps the full carryforward — the number a user copies onto form T1A/next year's return is overstated. The usa ledger has the same hole (line 194 `if y in claimed` inside the nets-years loop). The wrapper's auto-detected root claimed_losses.txt hits the identical path.
- **Expected**: The 2026 claim should reduce the final carryforward to 4.97 (or at minimum be surfaced under unmatched_claims / a warning that the claim year is outside the ledger).
- **Actual**: Ledger prints '2025 | -24.97 | 0.00 | 24.97' and 'Net-capital-loss carryforward after 2025: 24.97' — the 20.00 claim vanishes with no warning, no unmatched_claims line, exit 0.

```
# repro
cd /tmp/fc2-base (book: single 2025 loss year, carryforward 24.97)
printf '2026 20.00\n' > claimed_c.txt
python -m taxjson.bin.taxjson_run -C /tmp/fc2-base carryover --claimed claimed_c.txt
Compare: printf '2025 10.00\n2025 5.00\n' > claimed_a.txt → APPLIED 15.00 / CARRYFWD 9.97 (claims in an in-book year DO work).
```

### 25. USA tax estimate silently discards the losing side when ST and LT gains have opposite signs — overstated tax on very common data

- **Finder**: r2:web-pages-forms  |  **Area**: estimate-tax-pane
- **File**: src/taxjson/lib/tax_estimate.py
- **Detail**: estimate_usa never nets an intra-year ST loss against an LT gain (or vice versa). Line 220 `st_net, lt_net = max(0.0, st_net), max(0.0, lt_net)` clamps the negative category to 0 after the other-losses waterfall, and `ordinary_offset` only captures the NET-negative portion (`max(0.0, -net_cap)`), so when the year is net-positive with mixed signs the whole loss category vanishes. Schedule D requires netting ST against LT when signs differ. A year with ST -1,000 and LT +5,000 (net realized +4,000) is taxed on the full 5,000 LT: estimate $750 instead of $600 (25% overstated). Mirror case ST +5,000 / LT -1,000 gives $1,100 instead of $880. The printed line even contradicts itself: '(18.8% of 4,000.00)' quotes the NET investment income while the tax was computed on the gross 5,000. The GUI Tax pane (st_net/lt_net rows) shows the same wrong numbers. No test in tests/test_tax_estimate.py pins mixed-sign behavior, and the module docstring's disclosed simplifications do not include dropping intra-year losses (the net-negative branch shows cross-netting WAS intended). Partial-netting case st=-4000/lt=+1000 also double-counts: grants the full 3,000 ordinary offset AND still taxes 1,000 preferential.
- **Expected**: ST -1,000 nets against LT +5,000 -> 4,000 LT @ 15% = ESTIMATED TAX 600.00 (matching the 4,000.00 net the summary table and the avg-rate denominator both report)
- **Actual**: Output: 'Short-term gains (net) 0.00 [-1,000.00 before other losses]', 'Long-term gains (net) 5,000.00', '=> ESTIMATED TAX ON INVESTMENT INCOME: 750.00 USD (18.8% of 4,000.00)' — rc 0; the 1,000 ST loss disappears; mirror case returns 1,100.00 instead of 880.00

```
# repro
mkdir -p /tmp/estpane-us2/inputs/margin; write taxjson.toml ([settings] year=2025 country="usa" base_currency="USD" source_currencies=[] ; [accounts.margin] type="taxable") and inputs/margin/questrade.csv with: Buy 100 AAPL 2023-01-10 @100, Sell 2025-03-20 @150 (LT +5000); Buy 10 MSFT 2025-02-01 @400, Sell 2025-04-01 @300 (ST -1000). Then: python -m taxjson.bin.taxjson_run -C /tmp/estpane-us2 run --no-input && python -m taxjson.bin.taxjson_run -C /tmp/estpane-us2 sum --other-income 100000. Unit check: python -c "from taxjson.lib.tax_estimate import estimate_usa; print(estimate_usa(st=-1000,lt=5000,qualified_div=0,pil=0,other_income=100000,other_losses=0)['estimated_tax'])"
```

### 26. Negative --other-losses fabricates taxable capital gains: a loss year is shown owing $1,499.76 tax with exit 0

- **Finder**: r2:web-pages-forms  |  **Area**: estimate-tax-pane
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: --other-losses (type=float, taxjson_run.py:4838) and --other-income (:4833) accept negative values with no validation. estimate_canada (tax_estimate.py:157) computes net_gain = realized - other_losses, so a NEGATIVE loss ADDS phantom gains: project whose only activity is a -500.00 realized loss + --other-losses -10000 prints 'Capital gains (taxable) 4,750.00' and '=> ESTIMATED TAX ON INVESTMENT INCOME: 1,499.76 CAD', rc 0. Entering losses as negative numbers is the natural sign convention for many users ('my carryover is -10,000'), so this is a wrong-number trap, not just missing validation. --other-income -50000 is likewise accepted and renders a straight-faced all-zero estimate. The GUI spinboxes clamp at 0, so only the CLI is exposed. Same hole in the usa path (negative other_losses flows into the ST/LT waterfall).
- **Expected**: Error (rc != 0) for a negative loss amount, or at minimum treat the magnitude as a loss — never convert a user's losses into taxable gains
- **Actual**: rc 0; 'Capital gains (taxable) 4,750.00 [-500.00 realized - -10,000.00 other losses, x50%]' and '=> ESTIMATED TAX ON INVESTMENT INCOME: 1,499.76 CAD' for a year that lost money

```
# repro
python -m taxjson.bin.taxjson_run -C /tmp/estpane-ca sum --province ON --other-income 100000 --other-losses -10000  (project: canada/2025/CAD, one taxable account whose gains file has realized -500.00 and a 50.00 CAD dividend)
```


## MINOR

### 27. What-if doubles cost basis and gain when the book contains a real same-day sale with identical qty/price (content-hash id collision)

- **Finder**: web-ui  |  **Area**: web-ui
- **File**: src/taxjson/web/data.py
- **Detail**: what_if_sell builds a synthetic TaxTransaction whose id is the deterministic content hash (core.py compute_id). If work/<acct>_base.json already contains a real sale on today's date with the same symbol/qty/price (user sold 40 @ 15 this morning, imported, then asks 'what if I sell 40 more at 15'), the synthetic tx gets the SAME id as the real one. data.py:312 aggregates gain entries by `t.get("id") == synth.id`, so the real sale's disposition rows are summed together with the simulated one: cost_basis and gains are doubled and reported as ok:true. Repro: 100 sh @ 10 held, real sale today -40 @ 15; what-if sell 40 @ 15 returns cost_basis=800, economic_gain=400 (correct: cost 400, gain 200 — control at price 15.01 returns 200.4). The doubled `closed` count also weakens the oversell guard.
- **Expected**: Simulated sale of 40 shares with cost 10/sh at 15 reports cost_basis 400, economic_gain 200 regardless of what real trades already exist today
- **Actual**: cost_basis 800.0 and economic_gain 400.0 — the real sale's per-lot entries share the synthetic tx's content-hash id and are double-counted

```
# repro
cd scratchpad; python attack5.py — margin_base.json has buy 100@10 (2025-01-02) plus real sell -40@15 dated today; GET /api/whatif?account=margin&symbol=AAA.TO&qty=40&price=15 -> {"ok":true,..."cost_basis":800.0,"economic_gain":400.0}; control price=15.01 -> economic_gain 200.4
```

### 28. list --date accepts impossible calendar dates and silently returns wrong positions

- **Finder**: cli-options  |  **Area**: cli-options
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: cmd_positions validates --date with only a shape regex (line 3553: re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of)), never as a real calendar date. An impossible date like 2025-15-02 (a user who swapped day/month meaning Feb 15) or 2025-13-45 sails through, is passed to the gains engine as --as-of, and the engine's string comparison silently produces a different (wrong) cutoff. No warning, exit 0, and the banner even prints the bogus date as the basis label.
- **Expected**: Invalid calendar dates rejected with the existing 'taxjson list: --date expects YYYY-MM-DD' error (exit != 0), like the purely-shape-invalid '20250101' is.
- **Actual**: Both 2025-15-02 and 2025-13-45 exit 0 and print a full positions table; 2025-15-02 shows QTY 60 / COST 1,802.97 'as of 2025-15-02' instead of the QTY 100 / 3,004.95 the user meant — silently wrong numbers.

```
# repro
cd /tmp && python -m taxjson.bin.taxjson_run -C /tmp/cliopt-p2 list --date 2025-02-15   # correct: XIU.TO QTY 100, COST 3,004.95
python -m taxjson.bin.taxjson_run -C /tmp/cliopt-p2 list --date 2025-15-02   # swapped day/month typo
python -m taxjson.bin.taxjson_run -C /tmp/cliopt-p2 list --date 2025-13-45; echo $?
```

### 29. init --year is unvalidated: 0 is silently replaced by the current year; negative/absurd years scaffold projects whose reports are silently all-zero

- **Finder**: cli-options  |  **Area**: cli-options
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: cmd_init passes args.year to _render_init_config, whose line 1445 is `year=int(year) if year else date_cls.today().year` — so `--year 0` is silently swapped for the current year (the truthiness test conflates 0 with 'not given'), and any other value (-5, 1889, 2101, the plausible fat-finger 20255) is written to taxjson.toml without warning. Downstream nothing catches it: `taxjson run` exits 0 and `taxjson sum` happily reports 'tax year -5' with every column 0.00 even though the project has real 2025 gains — a typo'd year yields a clean-looking, silently empty tax report.
- **Expected**: init rejects (or at least warns about) years outside a sane range, and --year 0 should be an error, not a silent substitution of the current year.
- **Actual**: --year 0 silently becomes 2026; --year -5 / 1889 / 2101 / 20255 all exit 0 and write the value verbatim; the resulting project runs cleanly and reports zeros for every window.

```
# repro
rm -rf /tmp/cliopt-init && python -m taxjson.bin.taxjson_run init /tmp/cliopt-init --country ca --year 0 && grep ^year /tmp/cliopt-init/taxjson.toml   # -> year = 2026
python -m taxjson.bin.taxjson_run init /tmp/cliopt-init --country ca --year -5 --force && grep ^year /tmp/cliopt-init/taxjson.toml   # -> year = -5
# downstream: cp -r /tmp/cliopt-p2 /tmp/cliopt-p3; sed -i '' 's/^year = 2025/year = -5/' /tmp/cliopt-p3/taxjson.toml
python -m taxjson.bin.taxjson_run -C /tmp/cliopt-p3 run; echo $?   # 0
python -m taxjson.bin.taxjson_run -C /tmp/cliopt-p3 sum | head -6   # 'tax year -5', all 0.00
```

### 30. Accounts added to taxjson.toml while serving are invisible and the error message falsely claims they are not in taxjson.toml

- **Finder**: web-ui  |  **Area**: web-ui
- **File**: src/taxjson/web/app.py
- **Detail**: ProjectContext is loaded once at startup (app.py:27 create_app(ctx); server.py loads it before uvicorn.run) and every route reads accounts from that snapshot, while holdings/radar files ARE re-read per request. Workflow: keep `taxjson serve` running, add [accounts.tfsa] to taxjson.toml, drop its CSVs, run `taxjson run`, refresh the browser. The dashboard does not list tfsa (its fresh reports/tfsa_holdings.toml is ignored because holdings_accounts iterates the stale ctx.accounts), /api/holdings?account=tfsa returns 404 with reason "no account 'tfsa' in taxjson.toml (accounts: margin, rrsp)" — a factually false statement, since tfsa IS in taxjson.toml — and the freshness banner does not fire (reports are newer than inputs after the re-run), so nothing hints that a server restart is required.
- **Expected**: Config re-read (mtime check) or at minimum an error message that does not deny the account exists and tells the user to restart the server
- **Actual**: New account 404s with a message contradicting the on-disk taxjson.toml; dashboard silently omits it with no staleness hint

```
# repro
cd scratchpad; python attack4.py section B — start TestClient on project, then append [accounts.tfsa] to taxjson.toml + write reports/tfsa_holdings.toml + work/tfsa_base.json; GET /api/holdings?account=tfsa -> 404 {"ok":false,"reason":"no account 'tfsa' in taxjson.toml (accounts: margin, rrsp)"}; GET / -> 'tfsa' not in body
```

### 31. /api/whatif returns 500 Internal Server Error for qty or price = nan/inf, breaking the route's no-500 contract

- **Finder**: web-ui  |  **Area**: web-ui
- **File**: src/taxjson/web/app.py
- **Detail**: app.py:133-140 wraps what_if_sell in try/except with the comment 'surface as JSON, don't 500', but pydantic accepts qty=nan / price=nan / price=inf as valid floats, what_if_sell returns a dict containing NaN/inf, and Starlette's JSONResponse json.dumps(allow_nan=False) then raises ValueError('Out of range float values are not JSON compliant: nan') OUTSIDE the handler's try — a bare 500 'Internal Server Error' with a server-side traceback. The HTML POST /whatif path renders the garbage instead: 'Sell nan AAA.TO @ 15.0 ... Proceeds nan, Cost basis nan, Economic gain/loss nan' as a 200 ok-styled result.
- **Expected**: Non-finite qty/price rejected with the same friendly {"ok": false, "reason": ...} shape the route promises (or a 422)
- **Actual**: API 500s with an unhandled serialization ValueError; HTML page renders NaN in every money field as a successful result

```
# repro
cd scratchpad; python attack1.py & attack2.py & attack3.py — GET /api/whatif?account=margin&symbol=AAA.TO&qty=nan&price=15 -> 500 Internal Server Error (traceback: ValueError: Out of range float values are not JSON compliant: nan in starlette/responses.py render); same for price=nan and price=inf; POST /whatif with qty=nan -> 200 page showing 'Proceeds nan CAD'
```

### 32. Negative sale price accepted; what-if reports internally inconsistent numbers (proceeds -150, cost 100, gain +50) as ok

- **Finder**: web-ui  |  **Area**: web-ui
- **File**: src/taxjson/web/data.py
- **Detail**: Neither the /whatif form route nor /api/whatif nor what_if_sell validates price > 0. The holding-detail form's price input is type=number step=any, so a user typo like '-15' submits fine. data.py:291 computes proceeds = abs(qty) * price = -150 and feeds a negative-proceeds sell to the engine, which then reports gain +50 (as if the price were +15) while the response still carries proceeds=-150 and cost_basis=100. The rendered page states: Proceeds -150.0 CAD, Cost basis 100.0, Economic gain/loss 50.0, Deductible now 50.0 — figures that contradict each other (proceeds-cost = -250) — flagged neither as a loss nor as an error.
- **Expected**: price <= 0 (and qty <= 0) rejected with {"ok": false, "reason": ...} like other bad inputs (oversell, unheld symbol) are
- **Actual**: ok:true with mutually contradictory proceeds/cost/gain figures; the sign of the loss is inverted

```
# repro
cd scratchpad; python attack1.py / attack3.py — GET /api/whatif?account=margin&symbol=AAA.TO&qty=10&price=-15 -> 200 {"ok":true,"proceeds":-150.0,"cost_basis":100.0,"economic_gain":50.0,"is_loss":false}; POST /whatif data price=-15 -> 200 page showing 'Proceeds -150.0 CAD ... Economic gain/loss 50.0'
```

### 33. Large period tokens crash every period-aware command with an uncaught traceback

- **Finder**: cli-options (also found by: query-filters)  |  **Area**: cli-options
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: _tx_period_cutoff (lines 1682-1696) validates the token shape (\d+[dwmy]) but not the magnitude. Any N that pushes the cutoff before year 1 raises ValueError('year ... is out of range') at line 1696, and a huge day count raises OverflowError in timedelta at line 1689 — both escape as raw tracebacks (exit 1) on every command that takes a PERIOD positional (events/divs/roc/leaps/trades/gains, all -sum roll-ups, winners, fees, value, timeline, yield). Even '2026y' (a user typing the year with a stray y) crashes since 2026 years back is year 0, while '2025y' silently means 'last 2025 years'.
- **Expected**: The same clean one-line error other bad tokens get: "taxjson: invalid time period '99999m' (use e.g. 30d, 6w, 3m, 1y, ...)" — a usage error, no traceback.
- **Actual**: Full Python traceback ending in ValueError/OverflowError, exit 1, on every period-aware subcommand.

```
# repro
python -m taxjson.bin.taxjson_run -C /tmp/cliopt-p2 gains 99999m 2>&1 | tail -2   # ValueError: year -6307 is out of range
python -m taxjson.bin.taxjson_run -C /tmp/cliopt-p2 gains 9999999999d 2>&1 | tail -2   # OverflowError: Python int too large to convert to C int
python -m taxjson.bin.taxjson_run -C /tmp/cliopt-p2 winners 99999m 2>&1 | tail -2
python -m taxjson.bin.taxjson_run -C /tmp/cliopt-p2 divs-sum 999999y 2>&1 | tail -2
```

### 34. Successful single-account run deletes work/pending_elections.json for ALL accounts — elect --pending then falsely reports everything resolved

- **Finder**: elections-flow  |  **Area**: elections-flow
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: cmd_run unconditionally unlinks the aggregate pending file when the accounts it just staged had no pending elections (taxjson_run.py:1352), even under `--account X`. With two accounts both pending, resolving only margin and running `taxjson run --no-input --account margin` exits 0 and deletes work/pending_elections.json while broker2's election is still unresolved and its books unbuilt. `taxjson elect --pending` then prints 'No pending elections (... or they've been resolved).' — the ready-to-copy --set lines for broker2 (including its event id, which is otherwise awkward to obtain) are gone until the user thinks to do a full re-run.
- **Expected**: A single-account run should only remove that account's entry from the aggregate (or leave the file alone); elect --pending should still show broker2's unresolved event.
- **Actual**: File deleted; elect --pending claims everything is resolved while broker2 still cannot build.

```
# repro
Two-account project (margin + broker2, each with a merger CSV); taxjson run --no-input -> exit 3, pending file lists both; taxjson elect margin --set '75f5b42990df=ignore'; taxjson run --no-input --account margin -> exit 0; ls work/pending_elections.json -> No such file; taxjson elect --pending -> 'No pending elections'
```

### 35. Empty event id: --set '=ELECTION' writes an untargetable "" record, and --reset --event '' clears ALL elections instead of erroring

- **Finder**: elections-flow  |  **Area**: elections-flow
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: cmd_elect --set only checks that '=' is present (taxjson_run.py:1580), so `--set '=rollover_s_85_1_5'` saves a manifest record keyed "" (verified: {"elections": {"": {...}}}). That record can never be scoped by --event because `if args.event:` (:1628) treats the empty string as 'no --event given' — the same falsy check means `elect margin --reset --event ""` (e.g. --event "$ID" with an unset shell variable) silently drops the scoping and wipes EVERY election: observed 'Cleared 2 election(s)' leaving {"elections": {}}. Elections are the documented non-rebuildable user decisions, so an accidental wipe-all where the user asked to clear one event is a real data-loss footgun.
- **Expected**: Empty event id rejected at --set; --event '' rejected (or treated as an unknown id -> the existing 'no election ... for account' error), never widened to clear-all.
- **Actual**: ""-keyed record written with exit 0; --reset --event '' cleared all elections with exit 0.

```
# repro
python -m taxjson.bin.taxjson_run -C /tmp/p elect margin --set '=rollover_s_85_1_5'   # exit 0, writes key ""; then python -m taxjson.bin.taxjson_run -C /tmp/p elect margin --reset --event ''   # prints 'Cleared 2 election(s)', manifest now {"elections": {}}
```

### 36. `list --date` silently omits accounts whose base.json is missing — a position shown by plain `list` vanishes with no warning

- **Finder**: query-filters  |  **Area**: query-filters / list --date
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: In the --date path cmd_positions iterates config accounts and, when `work/<acct>_base.json` is absent and no explicit account was named, just `continue`s (taxjson_run.py:3579-3584) with no stderr note. Plain `list` reads resolve_gains_files and shows the account's positions. So switching the same command from current view to as-of view makes whole accounts disappear from the table and the base-currency TOTAL with rc=0 and no indication — a silent data drop in a partially built project (e.g. account added/run individually), where the no-date view proves the data exists.
- **Expected**: A stderr note like the sibling paths print ("no krypto_base.json — run `taxjson run` first; account skipped"), or a hard error matching the single-account behavior.
- **Actual**: The account and its positions silently vanish from the as-of listing and totals.

```
# repro
Zoo has work/krypto_gains.json + work/krypto_filled.json but no krypto_base.json; work/margin_base.json exists.
$ python -m taxjson.bin.taxjson_run -C /tmp/qf-zoo list  -> shows "krypto BTC 0.25 15,000.00 ..." and margin rows; total 15,130.00.
$ python -m taxjson.bin.taxjson_run -C /tmp/qf-zoo list --date 2025-04-01  -> only margin rows (AAA.TO, BADCALL..., F:CL.TO, LMN...), total 8,010.00; no note that krypto was skipped; rc=0.
```

### 37. trades/events FEE column is always 0.00 for Questrade accounts while fees/trades-sum/--json report the real fees for the same rows

- **Finder**: r2:native-views-roundtrip  |  **Area**: native-views-roundtrip
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: _tx_display_line (~line 1782) reads only tx['fee'], but the Questrade parser stores commissions under tx['commission']. The helper _tx_fee (~line 2484) exists precisely to sum both keys and is used by `fees` and `trades-sum`, but the display formatter used by `events`/`trades`/`divs`/`dil`/`roc` ignores it. Result: within one CLI, `taxjson trades margin` prints FEE 0.00 on every BUYSELL row while `taxjson fees` prints 9.99 per row / TOTAL 59.94 and `taxjson trades-sum` prints FEES 29.97/19.98/9.99 for the exact same transactions. `trades margin --json` even includes commission: 9.99 in each row, so the text view disagrees with its own --json output. (Note: the commission-vs-fee key split itself is a known limitation; the defect here is the same-CLI views disagreeing on identical rows because the display skips the existing _tx_fee helper.)
- **Expected**: The per-row FEE column in `trades`/`events` shows the same 9.99 fee that `fees`, `trades-sum`, and `trades --json` report for the identical transactions.
- **Actual**: `trades margin` prints 'BUYSELL 2025-01-06 00:00:00 XIU.TO 100 CAD 35 3,509.99 0.00' (FEE 0.00 on all 6 rows) while `fees` prints 9.99 per row, '6 fee(s); TOTAL FEES: 59.94 CAD', and `trades-sum` prints per-symbol FEES 29.97/9.99/19.98, total 'fees 59.94'.

```
# repro
mkdir -p /tmp/nvr2-nv/inputs/{margin,rrsp}; write taxjson.toml (year=2025, canada, CAD, source_currencies=[], accounts.margin taxable, accounts.rrsp sheltered) and inputs/margin/questrade_2025.csv with 6 Buy/Sell rows each Commission -9.99 plus one DIV row; python -m taxjson.bin.taxjson_run -C /tmp/nvr2-nv run --no-input; python -m taxjson.bin.taxjson_run -C /tmp/nvr2-nv trades margin; python -m taxjson.bin.taxjson_run -C /tmp/nvr2-nv fees; python -m taxjson.bin.taxjson_run -C /tmp/nvr2-nv trades-sum
```

### 38. `roc` and `dil` print zero bytes (rc 0) when no rows match, while every sibling view prints an explicit empty-state message

- **Finder**: r2:native-views-roundtrip  |  **Area**: native-views-roundtrip
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: In _run_tx_view (~lines 1960-2005), when no transactions match, out_lines and the footer dict are both empty, so nothing is printed at all. This hits `roc` (ADJUST-only) and `dil` (DIVIDEND_IN_LIEU-only), the two views most likely to be legitimately empty. Siblings handle it: `leaps` prints 'No LEAPS contracts found (...)', `gains` prints '(no realized gains in this window)', `roc-sum` prints 'No ACB adjustments in tax year 2025.', `dil-sum` prints 'No dividends in lieu in tax year 2025.'. A user checking whether their broker classified any return-of-capital cannot distinguish 'no ROC this year' from 'command/window mis-specified' — e.g. `trades 2024` in the account-name collision above also emits zero bytes, and the two failure modes look identical. The --json path is fine (rows: [], totals: {}, bad_dates: 0).
- **Expected**: An explicit empty-state line like the sibling views, e.g. '(no ADJUST rows in tax year 2025)'.
- **Actual**: `roc` and `dil` produce 0 bytes on stdout and stderr, rc 0; `leaps`, `gains`, `roc-sum`, and `dil-sum` on the same project all print explicit 'No ...' messages.

```
# repro
cd /tmp/nvr2-nv (project has no ADJUST or DIVIDEND_IN_LIEU rows); python -m taxjson.bin.taxjson_run -C /tmp/nvr2-nv roc; echo RC=$?; python -m taxjson.bin.taxjson_run -C /tmp/nvr2-nv dil; echo RC=$?; compare python -m taxjson.bin.taxjson_run -C /tmp/nvr2-nv leaps and roc-sum/dil-sum
```

### 39. carryover --claimed accepts 'YEAR nan' (and inf) — NaN poisons the claim ledger and silently inflates APPLIED to the full balance

- **Finder**: r2:filing-cluster  |  **Area**: filing-cluster/carryover
- **File**: src/taxjson/bin/taxjson_carryover.py
- **Detail**: load_claimed validates with `amount < 0` (line 70), which is False for float('nan') and float('inf'), so '2025 nan' passes float() and is summed into claimed[2025], making pending_claim NaN. min(balance, NaN) then returns balance, so APPLIED becomes the entire carryforward regardless of the real claims on other lines, and the NaN remainder is dropped from unmatched_claims (NaN > 0.005 is False). Every other malformed line ('2025 abc', '2025 1,000.00', negative amounts) is caught with a per-line warning — this one corrupts the output with zero diagnostics.
- **Expected**: '2025 nan' rejected like the other malformed lines with the `expected YEAR AMOUNT (amount >= 0)` warning; ledger shows APPLIED 15.00 / balance 9.97.
- **Actual**: No warning for the nan line; ledger silently shows APPLIED 24.97 / CARRYFWD BAL 0.00 and 'carryforward after 2025: 0.00' — both filing numbers wrong, exit 0.

```
# repro
cd /tmp/fc2-base
printf '2025 10.00\n2025 5.00\n2025 nan\n' > claimed_b.txt
python -m taxjson.bin.taxjson_run -C /tmp/fc2-base carryover --claimed claimed_b.txt
Baseline without the nan line (claimed_a.txt): APPLIED 15.00 / CARRYFWD BAL 9.97.
```

### 40. form-export --csv PATH crashes with a raw IsADirectoryError traceback when PATH is an existing directory, and the report text is lost

- **Finder**: r2:filing-cluster  |  **Area**: filing-cluster/form-export
- **File**: src/taxjson/bin/taxjson_form_export.py
- **Detail**: write_csv (line 323) calls path.open('w') with no error handling, and main() writes the CSV BEFORE printing the report text (lines 386-393). Pointing --csv at an existing directory (an easy mistake: `--csv reports` intending a file inside it) raises IsADirectoryError as an uncaught traceback, exit 1, and the Schedule 3/8949 report the user asked for is never printed either. An unwritable path (PermissionError) takes the same unguarded path.
- **Expected**: A clean error such as "taxjson-form-export: cannot write CSV: 'outdir' is a directory" with exit 2, and ideally still print the report to stdout.
- **Actual**: Python traceback ending "IsADirectoryError: [Errno 21] Is a directory: 'outdir'", EXIT=1, no report output at all.

```
# repro
cd /tmp/fc2-base && mkdir -p outdir
python -m taxjson.bin.taxjson_run -C /tmp/fc2-base form-export --csv outdir; echo EXIT=$?
```

### 41. find-missing-history --gen-phantoms FILE crashes with a raw IsADirectoryError traceback when FILE is a directory, after all per-account work completed

- **Finder**: r2:filing-cluster  |  **Area**: filing-cluster/find-missing-history
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: cmd_find_missing_history writes the merged phantom file with a bare `out.write_text(...)` at line 4499. If the argument is an existing directory (e.g. the user passes the project or inputs dir expecting the file to be created inside it) the whole command runs every per-account taxjson-gains subprocess, prints their output, then dies with an uncaught IsADirectoryError traceback and exit 1 — no phantom file, no clean message. Unwritable parent (PermissionError) is the same unguarded path. (The happy path was verified separately: --gen-phantoms to a real file path writes phantoms.json which round-trips through `taxjson run` without crashing.)
- **Expected**: Up-front validation of the output path: "taxjson find-missing-history: --gen-phantoms target is a directory" with exit 2 before doing any work.
- **Actual**: All sub-tool work runs, then a Python traceback ending "IsADirectoryError: [Errno 21] Is a directory: '/tmp/fc2-phantom/inputs'", EXIT=1.

```
# repro
Project /tmp/fc2-phantom (canada/2025, Questrade CSV whose ENB.TO sell has no matching buy; `taxjson run --no-input` done).
python -m taxjson.bin.taxjson_run -C /tmp/fc2-phantom find-missing-history --gen-phantoms /tmp/fc2-phantom/inputs; echo EXIT=$?
```

### 42. sum --json with nan/inf inputs emits literal NaN/Infinity — invalid JSON that crashes JSON.parse and is silently corrupted by jq

- **Finder**: r2:web-pages-forms  |  **Area**: estimate-tax-pane
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: _json_out (taxjson_run.py:1821) uses json.dumps with default allow_nan=True, so `sum --json --other-income nan|inf` writes bare `NaN` / `Infinity` tokens into the estimate block (trace_with.ti, fed_gross, tax_with.federal, ...). That is invalid per the JSON spec: node's JSON.parse throws SyntaxError ('"federal": Infinity ... is not valid JSON'), and jq silently rewrites NaN to null (verified: `.estimate.trace_with.ti` -> null), so strict --json consumers either break or get corrupted values with rc 0. Python's lenient json.loads means the GUI happens to parse it, and the GUI's spinboxes cannot produce nan, but the documented machine contract ('Machine output: pure JSON on stdout') is violated for any script/pipeline consumer.
- **Expected**: Reject nan/inf at the flag (see companion finding), or emit spec-valid JSON (json.dumps(..., allow_nan=False) would at least fail loudly instead of emitting garbage)
- **Actual**: rc 0 and stdout containing literal `NaN` / `Infinity` tokens; JSON.parse throws, jq maps NaN to null

```
# repro
python -m taxjson.bin.taxjson_run -C /tmp/estpane-ca sum --province ON --other-income inf --json > /tmp/inf.json; node -e "JSON.parse(require('fs').readFileSync('/tmp/inf.json','utf8'))"  -> SyntaxError, node rc 1. And: python -m taxjson.bin.taxjson_run -C /tmp/estpane-ca sum --province ON --other-income nan --json | jq '.estimate.trace_with.ti'  -> null (silent corruption)
```

### 43. --other-income nan/inf accepted: rc 0 with internally inconsistent 'authoritative' estimate (Tax with investments: inf but ESTIMATED TAX 0.00)

- **Finder**: r2:web-pages-forms  |  **Area**: estimate-tax-pane
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: argparse type=float parses nan/inf/-inf for --other-income/--other-losses (taxjson_run.py:4833/4838) and nothing downstream validates. `--other-income nan` prints 'Other income nan' with every tax line 0.00 (nan propagates into _bracket_tax; max(0.0, nan) comparisons collapse to 0.0), rc 0. `--other-income inf` prints 'Tax with investments: inf (federal inf + ON inf)' and 'Tax on other income alone: inf' yet concludes '=> ESTIMATED TAX ON INVESTMENT INCOME: 0.00 CAD' (inf - inf = nan -> max(0.0, nan) -> 0.0) — an internally contradictory block rendered as a real estimate. `--other-losses nan` is silently treated as 0.00 in the printed breakdown. This is the CLI sibling of the confirmed /api/whatif nan 500.
- **Expected**: rc != 0 with 'other income must be a finite number' (mirroring how --province ZZ errors rc 1)
- **Actual**: rc 0 both times; nan run shows 'Other income nan' + 'Tax with investments: 0.00'; inf run shows 'Tax with investments: inf (federal inf + ON inf)' followed by '=> ESTIMATED TAX ON INVESTMENT INCOME: 0.00 CAD'

```
# repro
python -m taxjson.bin.taxjson_run -C /tmp/estpane-ca sum --province ON --other-income nan ; echo rc=$?  and  python -m taxjson.bin.taxjson_run -C /tmp/estpane-ca sum --province ON --other-income inf ; echo rc=$?
```

### 44. Following the estimate's own advice ('set province under [settings]') makes every taxjson run warn that the key is unknown and ignored

- **Finder**: r2:web-pages-forms  |  **Area**: estimate-tax-pane
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: When no province is available, _tax_estimate_result exits with 'pass --province ON|BC|AB or set `province` under [settings] in taxjson.toml' (taxjson_run.py:3320-3323), and [settings] province IS honored by sum --estimate (verified: province="on" works, "zz" errors identically to the flag). But _SETTINGS_KEYS (taxjson_run.py:365) omits "province", so validate_config flags it: after adding province = "ON" exactly as instructed, every `taxjson run` prints 'taxjson: warning: taxjson.toml: unknown [settings] key 'province' is ignored' — telling the user the estimate's required key does nothing, when it in fact drives the estimate (and the GUI Tax pane's '(from config)' choice). Users will either delete the key (breaking their GUI/CLI estimates) or distrust the validator.
- **Expected**: A key the product tells you to set (and reads) must be in the validator whitelist — no 'unknown key is ignored' warning
- **Actual**: run prints: taxjson: warning: taxjson.toml: unknown [settings] key 'province' is ignored — while sum --estimate uses that exact key

```
# repro
python -m taxjson.bin.taxjson_run -C /tmp/estpane-ca sum --estimate  (rc 1, tells you to set province in settings); add `province = "ON"` under [settings] in /tmp/estpane-ca/taxjson.toml; python -m taxjson.bin.taxjson_run -C /tmp/estpane-ca run --no-input 2>&1 | grep province
```

### 45. winners --top silently rewrites invalid values: --top 0 becomes 10, --top -3 becomes 1, no error

- **Finder**: r2:web-pages-forms  |  **Area**: estimate-tax-pane
- **File**: src/taxjson/bin/taxjson_run.py
- **Detail**: cmd_winners line 2424: `top = max(1, int(getattr(args, "top", None) or 10))`. `--top 0` is falsy so it silently becomes the default 10 (a user asking for zero rows gets ten with no message); any negative value is silently clamped to 1 by max(). Header then advertises the rewritten number ('top/bottom 1' for --top -3, 'top/bottom 10' for --top 0), never what the user asked for. This is the same missing-validation hole class as the estimate flags (argparse type=int, no range check) rather than an intentional convenience — sibling flags like --province do error on bad values.
- **Expected**: rc != 0 'argument --top: must be a positive integer' (argparse-level check), matching how invalid --province values error
- **Actual**: rc 0 both times; --top 0 silently shows 10 rows ('top/bottom 10'), --top -3 silently shows top/bottom 1

```
# repro
python -m taxjson.bin.taxjson_run -C /tmp/estpane-us winners --top 0  (header: 'top/bottom 10', rc 0) and python -m taxjson.bin.taxjson_run -C /tmp/estpane-us winners --top -3  (header: 'top/bottom 1', rc 0)
```


## Refuted (for the record)

- (import-run-e2e) Deleting an account's inputs/ folder after a successful run leaves its stale gains/positions in `taxjson sum`/`list` — the GUI keeps showing the deleted account's numbers while run reports success — 
- (frozen-seam) `taxjson show` crashes (rc 1, no output) under the default in-process dispatch with captured stdout — works under TAXJSON_DISPATCH=subprocess — Mechanism confirmed (in-process run_cmd capture of `show` → AttributeError rc=1 vs subprocess rc=0; reproduced), and it is not pinned as intended nor recorded in the audit/fuzz files. However it fails the run's defect ba
- (r2:native-views-roundtrip) An account named like a period token ('2024', '1y', 'mtd', 'ty') is silently reinterpreted as a time window — its data becomes unreachable and wrong rows print with exit 0 — The period-vs-account sniffing is documented intended behavior and is test-pinned: tests/test_arg_conventions.py declares "a literal YYYY is a valid period token" and test_year_token_on_gains_view_scope asserts `events 2
