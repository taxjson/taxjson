# Audit 2026-07b — UI Consistency, Tests, Features, Architecture

Five parallel auditors over the post-refactor tree (commit e7ea542 era, suite 1180 green).
Companion to `AUDIT-2026-07-design.md` (structural refactor — since executed) and
`KNOWN_ISSUES.md`. Exclusion lists from both were honored; nothing here re-reports a
known limitation or an already-done item.

## Executive summary — what matters most

Nine findings are **correctness-grade** (wrong numbers or wrong behavior a user can hit
today), the rest are alignment/consistency work. Ranked:

1. **Cross-command wash-basis drift** (§4 N1): `taxjson sum` and `taxjson list` read
   *pre-wash* gains files while `carryover` / `t1135` / `wash-sales` / `leaps` prefer
   *post-wash* — whenever a cross-account wash disallowance fires, the REALIZED total in
   `taxjson sum` disagrees with the filing commands, and no output says which basis it
   used. Six hand-copied glob blocks, two policies.
2. **US crypto losses wrongly wash-denied** (§3 #4): for `country = "usa"`, the crypto
   account runs the same §1091 wash logic as equities, but the IRS treats crypto as
   property — wash sales don't apply. The engine has `--no-wash`, `taxjson run` never
   passes it. Overstates tax for every US crypto filer.
3. **Web what-if on sheltered accounts is double-wrong** (§5 #4): the simulated account
   is loaded as the taxable book *and again* inside the sheltered pool (its own buys act
   as their own wash triggers), and wash detection is hard-enabled regardless of account
   type — the CLI's `effective_detect_wash()` policy is bypassed.
4. **Web routes + xlsx tool have zero effective test coverage** (§2): the venv lacks
   `httpx` (so `fastapi.testclient` import fails → web-route tests skip) and `openpyxl`
   (all 4 xlsx_to_csv tests skip). Both directions of the web extras gate skip. CI likely
   mirrors this.
5. **`cmd_run` never executed by any test** (§2 #1): every 2026 stage is unit-tested, but
   the orchestrator wiring (config→stages, cache markers, ordering) has no end-to-end pin.
6. **Wash radar still carries a private sixth event-ordering ladder** (§4 N2): the
   `event_sort_key` centralization missed `taxjson_wash_radar.py:get_tx_priority` and the
   radar's parallel ACB replay — the file with the highest fix-ratio in the repo is the one
   still unlinked from the shared ordering.
7. **`lib/pipeline.py:192` calls `sys.exit(1)` from library code** (§4 N7): the last one
   left in `lib/`; the web server survives only via a magic `taxable=False` flag.
8. **Home-dir price caches never expire and escape `--force`** (§4 N6):
   `~/.crypto_price_cache.json` and `~/.currency_price_cache.json` — a wrong-but-nonzero
   cached price survives `rm -rf work/` forever; no inspect/clear command.
9. **Error messages cite nonexistent subcommands** (§1 C2): `taxjson summary:` (real name
   `sum`) at taxjson_run.py:2322, `taxjson positions:` (real name `list`) at :2411,2419.

**The UI theme** (the request): the CLI surface grew one tool at a time and it shows —
six section-banner dialects, three table renderers, `money()` copy-pasted 10× inside
taxjson_run.py alone, money at 4 decimals in leaps/ccd views, six spellings of "the input
gains files", three ANSI-color gates, five warning-prefix styles, and `taxjson fees` ≠
`taxjson-fees` (the script is actually `fees-sum`). §1 proposes one convention per axis;
the fulcrum move is a shared `lib` report module (`fmt_money`, `render_table`,
`resolve_gains_files`, `add_price_chain_args`) that N1/N4/B2/B4/A-axes all land on.

**Suggested execution order** (each S unless noted):
1. C2 wrong-name errors + N7 lib sys.exit (trivial, do first).
2. `resolve_gains_files(prefer_wash)` + basis stamp in output → fixes finding 1.
3. `--no-wash` for usa+crypto accounts → fixes finding 2.
4. Web what-if sheltered fix + holdings-TOML 500 + account validation (§5 items 1-3).
5. Dev-deps: add httpx + openpyxl to the dev/test extras; re-enable web-route tests;
   add `/api/*` to the smoke list; end-to-end `taxjson run` test (M).
6. Shared report module + port the renderers (M) — B2/B3/B4/B5 in one release, with a
   changelog note (reports/*.sum and *.rpt shapes churn).
7. Radar onto an `event_sort_key` profile + equivalence test.
8. Diagnostics normalization (C1/C3/C4) + argparse alignment with deprecated aliases (A1-A4).
9. Feature track (user-prioritized): interest income/expense split (S), affiliated
   wiring (S-M), FTC summary (M), harvest view (M), `run --year` (S-M).

---

## §1. CLI UI consistency

Scope: all 37 console scripts (pyproject.toml:98-137), all 28 `taxjson` subcommands
(taxjson_run.py:3147-3448), every bin/*.py argparse + renderer.

Axis ranking by user impact: **B output format > A argument conventions > E naming >
C diagnostics > D help text**.

### B. Output format

**B1. Section banners — 6 dialects.** `=`-rule sandwiches at widths 181 (sum_gains:205),
145 (leaps_gains:85, ccd_gains:89), 100 (sum_income:177, lint_crosslistings:143), 78
(t1135:445, carryover:230, reconcile_slips:244, form_export:262), 60/54/72 (atr:62,
sum_gains:295, corp_actions:98 — on stderr!); `--- Title ---` (sold_performance:230,
safe_to_sell:113); ljust-padded `--- TITLE (n) ---` (wash_radar:512); plain title line
(taxjson_run.py:2389, 2074, 2577); `==>` progress banners (run.py:615...); `# === X ===`
(diff:237). **Convention:** the taxjson_run.py core style — one title line
`REPORT NAME — scope`, blank line, no sandwich; `==>` reserved for pipeline progress.

**B2. Table rendering — 3 dialects.** Space-aligned + `-` rule
(run.py `_print_report_table` :1587-1603, sold_performance, sum_gains, sum_income,
missing_history); ` | `-piped with `-+-` rules (t1135:489, carryover:266, form_export:255,
wash_radar:484, atr:79); raw fixed-width f-strings (fees:212, leaps_gains:89, ccd_gains:93).
**Convention:** extract `_print_report_table`/`_align_columns` to a shared lib module,
use everywhere; `-` rule before the totals row.

**B3. Totals rows.** `TOTAL` (run.py:2374, sold_performance:240, fees:247, leaps:120,
ccd:124) vs `TOTALS` (sum_gains:285) vs `SUBTOTALS`/`TOTAL NET INCOME` (sum_income:230,245)
vs prose (`TOTAL GAIN: …`, `>>> TOTAL {und} LONG OPTION GAIN:`). **Convention:** in-table
row labelled `TOTAL`, ruled off; optional one-line prose recap starting `TOTAL …:`; drop `>>>`.

**B4. Money.** Commas+2dp in the new tools; no commas in sum_gains/sum_income/
sold_performance; **4 decimals** in leaps_gains:95-121 and ccd_gains:98-124; `money()`
helper defined 10× in taxjson_run.py (:1536,1808,1920,2044,2087,2123,2169,2217,2325,2422)
plus `_money` clones in fees/t1135/carryover. Currency label: suffix `1,234.56 CAD`
(majority) vs prefix `CAD 1,234.56` (t1135:452) vs `TOTAL [CAD]` (fees:272).
**Convention:** one shared `money()` = `{:,.2f}`; currency as suffix; 4dp only for
per-share/qty.

**B5. Header vocabulary.** ALL-CAPS everywhere except sold_performance:229 and atr:79;
`SYMBOL` vs `TICKER`; `CUR` vs `CURR`; `[ST: x LT: y]` bracket only in sum_gains:272,282
where everything else uses columns. **Convention:** ALL-CAPS; SYMBOL, CUR, QTY, COST,
PROCEEDS, GAIN, DAYS; ST/LT as optional columns.

**B6. "No data" text.** Ten variants, one of which (`atr:68`) goes to stdout as data and
its sibling (`hv:48`) to stderr as an Error. **Convention:** sentence-case
`No <things> <scope>.` on stdout, exit 0; `(none)` only for empty sections inside a report.

**B7. Color — 3 gates.** sum_gains (default-on, ignores NO_COLOR), diff (default-on,
honors NO_COLOR — the correct model), explain (default-off `--color`, hidden `--no-color`).
**Convention:** color iff `isatty and not NO_COLOR and not --no-color`, everywhere.

### A. Argument conventions

**A1. Time scoping.** Positional `period` required on events/divs/roc/leaps/trades/gains
but optional on the -sum variants, with two different help strings; `fees-sum` has BOTH
`--year` and `period` with a precedence footnote; standalones use `--year` as int except
taxjson_fees.py:357 where it's a string, plus a unique `--since`. **Convention:** optional
positional PERIOD (default: tax year) on all roll-up subcommands, one canonical help
string; `--year INT` on standalones; retire `fees-sum --year` and `taxjson-fees --since`.

**A2. Input files — six shapes.** `files nargs="*"` / `inputs nargs="+"` / `input` /
named positionals (`base_files`, `gains_files`, `slip_csv`) / flag `nargs='+'`
(wash_radar `--taxable/--sheltered`) / flag `action="append"` (t1135 `--gains`,
carryover `--sheltered`); taxjson_gains.py:52 `--sheltered` takes exactly ONE path while
its siblings take many. **Convention:** primary inputs = positional `files` metavar FILE;
auxiliary sets = repeatable `--gains FILE` append-style.

**A3. `--json`.** Present in the five new filing tools + run wrappers; spelled
`--json-out PATH` (file, still prints text) in wash_radar:96; absent from sum_gains,
sum_income, sold_performance and every period roll-up. **Convention:** `--json` = JSON to
stdout, one help string; add to the sum tools.

**A4. Same flag, different meaning.** `--cache` = parsed-tx DIR (fees:355) vs price-cache
FILE (sold_performance:93 — rename `--price-cache`); `--base-currency` default "CAD" vs
"" vs None; `--country` silently defaults to canada in standalone gains/carryover while
`init` made it required; reconcile tolerance default duplicated (1.00 in the tool,
re-documented as prose in the run wrapper); `--account` vs `--account-name`; `-v` alias
only in wash_radar.

### E. Naming

`taxjson fees` ≠ `taxjson-fees` (the script is the fees-**sum** report — actively
misleading; rename with a deprecated alias). `sum`↔`sum-gains`, `divs-sum`↔`sum-income`,
`sold-perf`↔`sold-performance`, `find-missing-history`↔`missing-history`,
`leaps`↔`leaps-gains`/`leaps-missed`; `taxjson-explain`/`taxjson-diff` have no subcommand
despite wash-sales shelling into explain (run.py:2804); `serve` vs `[web]` extra. Three
bin modules lack the `taxjson_` prefix (to_base_curr, fill_crypto_prices, xlsx_to_csv).
Terminology: TICKER/SYMBOL, gain/realized, "wash sale" used in CRA context
(wash_radar:552-557). **Convention:** subcommand name is canonical; scripts are
`taxjson-<subcommand>`; SYMBOL in tables; "superficial loss (wash sale)" on first mention
in Canada output.

### C. Diagnostics

**C1. Prefixes:** `warning:` (dominant) vs `Warning:` (merge:22) vs `WARNING:`
(fill_crypto_prices:53, leaps_missed:539) vs `[WARN]/[ERROR]` (validate:145) vs
`Error:`-capitalized (8 tools); `taxjson: warning:` in run.py:592 vs bare `warning:` in
the same file (:531). **Convention:** GNU style on stderr: `<prog>: warning|error|note: …`.

**C2. Wrong names in errors (bug-grade):** run.py:2322 says `taxjson summary:`,
run.py:2411,2419 say `taxjson positions:` — neither subcommand exists.

**C3. Streams:** atr prints "No data" to stdout, hv to stderr; validate splits ERROR
detail across both; corp_actions prints a full report banner to stderr. **Convention:**
data→stdout, diagnostics→stderr, "no data" is a stdout report line.

**C4. Exit codes:** brokerage uses 2 and 3; corp_actions 2 for data errors;
reconcile-slips 2 missing-file / 1 mismatch; explain exits 1 on "no match" while
gains/fees/list/wash-sales exit 0. **Convention:** 0 = success incl. no-data; 1 = the
tool's finding (mismatch/violation); 2 = usage/environment.

### D. Help text

Imperative-sentence descriptions (majority) vs product-name titles
(safe_to_sell:29, wash_radar:90 — doesn't say "wash", hv:82) vs none
(convert_currency:209, fill_crypto_prices:57); UPPER metavars vs `input_file`/`Y`;
`%(default)s` used exactly once (sold_performance:98). **Convention:** one imperative
sentence; UPPER metavars from {FILE, DIR, NAME, CURR, YYYY, DATE, N};
`(default: %(default)s)` on every defaulted option.

---

## §2. Test coverage

Suite: **unittest** (not pytest), 1180 tests OK, 10 skipped, ~10.5s. `coverage` not
installed — static analysis used.

**Environment finding:** the venv lacks `httpx` and `openpyxl`. Consequences:
- Web-route tests skip (`_HAVE_WEB=False`, test_web_data.py:163-168) AND the inverse
  missing-extra test skips because uvicorn IS installed — both directions of the gate
  skip, so `web/app.py` routes and `web/server.py` are never exercised anywhere.
- All 4 xlsx_to_csv tests skip — the tool has zero effective coverage.

**Coverage map (abridged):** core.py, corp_actions math, five of six parsers, pipeline,
corporate_timeline, report_model, price_chain, the 2026 filing tools — well covered.
Partial/untested: `cmd_run` orchestrator (run.py:1049-1186 — never executed), `cmd_show`,
`cmd_serve`, sum_gains aggregation (only the convergence banner is tested; the new TOTAL
column from d7fdc1a has zero tests), sum_income, sold_performance, ccd_gains/leaps_gains
standalones, coinbase (thinnest parser), safe_to_sell (0 tests, pure logic,
tax-consequential), analytics quartet (network-bound, low value).

**Top missing tests (ranked):**
1. End-to-end `taxjson run` on a scaffolded project — pins stage ordering, config→stage
   wiring, cache markers in one shot.
2. Restore web-route tests (add httpx to dev deps or gate on fastapi import) + add
   `/api/holdings`, `/api/whatif` to the smoke list.
3. CRA wash-solver non-convergence fallback (core.py:1449-1462) — warning emitted AND
   gains promoted, not dropped.
4. sum_gains aggregation math incl. TOTAL column.
5. safe_to_sell 30-day boundary (day 30 in / 31 out) + taxable/sheltered filtering.
6. xlsx_to_csv actually running (openpyxl in dev extras or tiny committed fixture).
7. cmd_show happy path + missing-report exit.
8. Questrade split-row warning paths (questrade.py:262,272).
9. corp-actions warning emissions (991, 1226/1235, 722/749) — only signal for the
   usa+RBC-merger class.
10. negative days_held warn+clamp (core.py:970-981).
11. price_chain stale-cache + unwritable-dir warnings.
12. FX refresh-failure continues-with-cached path (run.py:531-534).
13. wash-radar malformed-tx skip warning (:169).
14. cmd_serve missing-extra branch via sys.modules poisoning.
15. ccd/leaps_gains error paths.

**Quality notes:** web smoke asserts only status 200; sum_gains convergence tests pin the
banner not the sums; several asserted stderr warnings leak un-silenced into green runs
(conformance's redirect_stderr pattern is the model); fixture realism is otherwise good
(golden + activity-matrix per broker, `UPDATE_GOLDEN=1` flow).

---

## §3. Feature gaps (new only; board items excluded)

1. **Wire `--affiliated` into `taxjson run`** (S-M) — the engine and pipeline already
   thread affiliated-person transactions (taxjson_gains.py:57-64, pipeline.py:214-254);
   run.py never passes it and the account schema has no partition for it. The canonical
   Canadian superficial-loss trap (spouse rebuys) is invisible. Add `type = "affiliated"`
   accounts + one DIAGNOSTICS advisory line when no affiliated data is configured.
2. **Foreign tax credit summary `taxjson ftc`** (M) — withholding is parsed per security
   from every broker and a per-security country classifier already exists for T1135
   (classify_country + t1135.map). Join them: per-country gross foreign income, tax
   withheld, effective rate → T2209 / Form 1116 feed.
3. **Split interest income from margin-interest expense** (S) — parsers keep the sign
   (rbc_direct.py:417-425), sum_income nets them into one CASH INTEREST line
   (:119-122). They're different tax lines in opposite directions (CA 12100 vs 22100;
   US Sch B vs Form 4952). Display split only.
4. **Stop applying §1091 to US crypto** (S) — **correctness**: run.py:741-751 passes the
   same flags to crypto accounts; IRS treats crypto as property, wash rule doesn't apply;
   `--no-wash` exists and is never passed. Canada is correct as-is.
5. **`taxjson harvest` — unrealized/tax-loss-harvest view** (M) — holdings TOML + price
   chain + radar sidecar (absolute clears_at) already exist; one table: unrealized G/L,
   would-be-superficial flag + clear date, US days-until-LT. sold-perf's machinery
   pointed at open positions.
6. **`taxjson run --year` + year-dimensioned reports dirs** (S-M) — today a prior-year
   run means editing the config and clobbering the current year's reports.
7. **In-kind registered contribution** (M) — taxable TRANSFER-out + sheltered TRANSFER-in
   pair → synthesize SELL-at-FMV + sheltered BUY with s.40(2)(g)(iv) denial, instead of
   the current hard error demanding hand-fabricated history.
8. **TXF export for TurboTax** (S-M) — records 712/714 map 1:1 onto rows form-export
   already computes.
9. **Income-slip reconciliation (T5/T3/1099-DIV)** (M) — dispositions have
   reconcile-slips; income (now more divergence-prone after ROC reclass) has nothing.
10. **T5008/1099-B as last-resort input** (M) — for unsupported/defunct brokers; the
    loose slip parser already exists in reconcile-slips.
11. **Docs: ~9 shipped CLIs invisible in README** (S) — the standalone-tools table lists
    3 of 40 entry points; generate-parser (the extensibility headline), xlsx-to-csv,
    detect-brokerage, validate, the analytics quartet are undocumented.

Smaller: dual-filer `--country` run override (once #6 exists); web dashboard could link
generated reports; instalment projection judged out of scope.

---

## §4. Architecture & bug clusters

**Bug-density (126 commits since 2026-01):** taxjson_run.py 51 commits/19 fix — hot-active,
fragility incubating in the copy-pasted query commands; core.py 38/19 — root causes
addressed, expect cooling; ib_extractor 23/11 — still fragile (known-open ~820-line
parse_file); **wash_radar 22/12 — highest fix ratio in the repo**, a parallel mini-engine
where every engine fix must be manually mirrored; rbc_direct 22/6 — active development.
Post-refactor fragility has migrated to (a) the two remaining parallel replicas (radar,
IB parser) and (b) run.py's query-command growth zone.

**New findings:**
- **N1 (S)** Wash-preference drift: six copies of gains-file discovery, two policies —
  post-wash in cmd_leaps:1848/cmd_wash_sales:2512/cmd_t1135:2631/cmd_carryover:2718;
  pre-wash-only in cmd_summary:2318/cmd_positions:2415. Fix: `resolve_gains_files(...,
  prefer_wash)` in report_model + a "basis: post-wash" stamp in output.
- **N2 (S/M)** Radar's private ordering ladder (wash_radar.py:54-59) + parallel ACB
  replay (:173-240) — add a radar profile to event_sort_key, pin equivalence; shrink the
  replay later.
- **N3 (M)** lib→bin layering inversion: report_model.py:101 lazily imports bin modules
  that import report_model at module level; also web/data.py:150,247, merge2:37-45,
  reconcile_slips:47, fees:41. Move `summarize_*`, rate helpers, map loaders into lib.
- **N4 (S)** money() ×10 in run.py + clones (see §1 B4).
- **N5 (S)** OCC regex stragglers with different semantics: pipeline.py:451,
  sold_performance:64, lint_crosslistings:40 (different strike floor), ticker_map:71
  (IGNORECASE), rbc_direct:324 — replace with core's `is_option_symbol`.
- **N6 (S)** Three price caches, three policies: work/.price_cache.json (age-warned) vs
  ~/.crypto_price_cache.json and ~/.currency_price_cache.json (global, never expire,
  survive --force and rm -rf work/). Document + `taxjson cache --clear-prices`.
- **N7 (S)** pipeline.py:192 `sys.exit(1)` from lib — the last one; raise typed error.
- **N8 (S)** Suffix-vocabulary copies GREW post-audit: reconcile_slips:49, t1135:27/526,
  leaps_missed:165 minted fresh lists that already disagree (V/CN/NE coverage).
- **N9 (S-M)** Yahoo-symbol normalization ×3 (sold_performance:131-140,
  fill_crypto_prices:27-33, price_chain's own tier) — price_chain should own it, with
  yf_ticker.map as the single override file.
- **N10 (S)** Web re-implements the FX 5-day lookback (web/data.py:163-171 mirrors
  convert_currency:112-115) — export `has_rate_near()`.
- **N11 (S)** IBKR connection flags differ per tool (sold_performance `--ibkr-host` vs
  leaps_missed `--host` with hardcoded defaults) — one `add_price_chain_args(parser)`.

**Structure:** split run.py's ~1,450-line query-command half into one module, forcing the
shared-helper extraction (that's N1/N4 as a bugfix, not aesthetics); do NOT split core.py
CA/US into files (boundary already clean; the R1#3 hoist plan will reshape it anyway);
do NOT build the declarative stage graph unless --dry-run/parallel accounts become wanted.
Riskiest couplings: the lib→bin inversion; prepare_books' taxable-flag-as-process-killer;
`_CONFIG_PATH` module global (run.py:251,1057 — needs_rebuild silently loses config
invalidation for any caller that forgets it); radar's unlinked replay.

**Error handling:** substantially cleaned up — no bare `except:` in lib/, remaining broad
catches are deliberate and logged. Remaining: N7, and sold_performance:113-115 reporting
on partial data after a swallowed bad input.

Known-open items confirmed unchanged (one-liners): currency⇒suffix map copies (growing,
N8), IB parse_file monolith, leaps/ccd twins (engine now emits `direction`, fallbacks are
legacy-only), safe_to_sell unwired third ordering, mtime-cache over-invalidation +
deleted-input blindness, uncached back-half of run (stage_wash_pass + 17-file export
matrix), wash-record identity links, CA/US god-methods, run_capture stderr policy,
raw CalledProcessError stage-failure UX.

---

## §5. Web UI

Stack: FastAPI + Jinja2, `taxjson serve` (run.py:3426 → web/server.py). Data layer is
mostly shared with the CLI (holdings TOML verbatim, radar JSON sidecar preferred,
what-if on pipeline.prepare_books + shared FX/ticker-map loaders) — the refactor landed.

**Feature parity:** holdings, wash-radar, and what-if exist; sum/income/fees/wash-list/
roc/leaps/carryover/t1135 do not. Notably `work/<account>_report.json` was created *for*
the web (report_model.py:80-119) and the web never reads it — a gains/income dashboard is
nearly free.

**Ranked fixes:**
1. **(Correctness)** What-if on sheltered accounts: data.py:209-213 iterates
   `ctx.sheltered()` without excluding the simulated account (its own buys become its own
   wash triggers) and :264 hard-sets `detect_wash_sales=True` regardless of account type —
   gate on `effective_detect_wash()`.
2. **(Robustness)** Corrupt holdings TOML → unhandled TOMLDecodeError → 500 on `/`,
   `/holdings`, `/api/holdings` (data.py:33; dashboard calls it per account) — one bad
   file takes down the whole dashboard.
3. **(Security)** `?account=../../x` traverses (suffix-constrained) via data.py:30,72,94,204;
   no validation against ctx.accounts; no TrustedHostMiddleware → DNS-rebinding can read
   `/api/holdings` on localhost. Validate account + allowed_hosts.
4. **(Parity)** Dashboard gains/income from the already-written report.json.
5. **(Drift)** `_has_recent_rate` hardcoded 6-day mirror + literal `Decimal("1.35")`
   fallback (data.py:155-172) — derive from convert_currency + project config.
6. **(Terminology)** "Wash-sale radar"/"Superficial loss?" not keyed off ctx.country;
   raw `SHORT_TERM`/`LONG_TERM` rendered where CLI says ST/LT.
7. **(Formatting)** Raw floats in templates (`12345.6`), unlabeled native-currency
   `Total cost` column between differently-denominated columns.
8. **(Consistency)** Empty radar sections dropped, contradicting the CLI's deliberate
   `VIOLATION (0)` visibility; `.rpt`-fallback countdowns served stale with no marker.
9. **(Freshness)** Staleness scan misses ticker.map + phantoms.json (pipeline inputs).
10. **(Polish)** ProjectContext.load errors as raw tracebacks at startup; `"margin"`
    default hardcoded twice (app.py:67, data.py:64).

Good news: no debug mode, localhost-default bind with warning, autoescaping on, no |safe,
what-if error states all render instead of 500ing.
