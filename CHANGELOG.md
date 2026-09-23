# Changelog

## Unreleased

Canada rules, from a design + impact study and an adversarial audit of
the engine against the Act (docs/design/canada-rules-2026-09.md):

- **Option premium timing (ITA s.49(1)–(4)).** A written option's
  premium is a capital gain in the year WRITTEN; a buy-back is a loss in
  its own year; expiry adds nothing; an assignment folds into the share
  leg with no grant record (the s.49(4) post-amendment state). New
  `[settings] option_premium_timing = "grant" | "close"` (Canada default
  grant; the US engine keeps close = §1234) and
  `option_grant_timing_since = YEAR` (contracts written earlier keep
  close timing — the transition from books filed the old way; default:
  the project year). Same-year round trips are unchanged in total; only
  year-straddling contracts move. Threaded through run, the raw pass,
  wash pass, blended pass, audit, explain, close-year/check-filed and
  the web what-if; `summary.option_premium_timing` records the choice.
  Grant records carry `grant: true` and a note; `audit` shows WRITE.
  `option_buyback_loss_superficial` (default false) decides whether a
  buy-back loss is fed to the superficial-loss rule; the strict reading
  permanently denied an 18.5k loss on a real 30-second order
  correction because a LIRA held the same series.
- **`taxjson option-boundary`**: every written option whose write and
  close straddle a tax-year boundary (or that is open at year end), with
  where each amount lands and — using the `filed/` locks — whether a
  filed year needs a T1-ADJ (assignment after the grant year was filed).
- **s.40(3) deemed gain**: a return of capital that drives ACB below
  zero is booked as a qty-0 gain in the distribution year, ACB reset to
  nil (was a warning; the whole amount landed in the sale year).
- **s.54 short sales**: a new short sale or written option is not an
  acquisition and never triggers a superficial loss on a cover loss (the
  US §1091(e) re-short branch applied before); a long purchase held at
  day 30 still does, and its bump lands on that long holding.
- Documented (KNOWN_ISSUES) from the audit: superficial-loss attribution
  order between taxable and registered triggers; the s.40(2)(g)(iv)
  path; second-order denials from the bump date; the estimate's
  suffix-based dividend classification; interest expense not surfaced;
  spin-off wording; pre-2001 loss rates; `days_held` on trade dates.
  REFERENCES: s.39(1.1) for fx-cash, short sales' income character.
- `find-missing-history`: the phantom walk pooled positions per
  (symbol, account, CURRENCY) and had no buy-before-sell tie-break at
  equal timestamps, so a Norbert's-gambit pair (sell DLR.TO in CAD, buy
  DLR.U.TO in USD the same morning, one symbol after the ticker map)
  read as a 5,140-share phantom short "affecting 2025" while the engine
  had matched every sale correctly. Pools are per (symbol, account) like
  the engine's, and buys sort before sells at equal times.
- Questrade: a US-listed security bought in a CAD-only account (RESP)
  is settled in CAD with `EXCHANGE RATE r` in the description — Price and
  Gross Amount are USD, Net Amount is the CAD paid, and the Currency
  column says CAD. The parser filed such buys as `.TO` (a CDR-shaped
  symbol the `DISTINCT` rule then kept apart from the real US pool) with
  the USD gross taken as the CAD cost, under-stating the ACB by the
  whole exchange rate (real AVGO/GS/CAT/BABA rows, 2026-09-18). They are
  now the `.US` listing, costed at the CAD actually paid, and their
  dividends key to the US listing too.
- Kraken: the 2026 ledger format (new `amountusd` / `feeusd` /
  `balanceusd` / `feecurrency` columns) parses as before, and
  `amountusd` — Kraken's USD valuation at credit time — now prices
  staking rewards at the parser (income and the acquisition cost of the
  rewarded coins alike) instead of leaving them to the price filler,
  which had no quote for HYPE and booked those rewards at $0. Earn
  allocation / deallocation / autoallocation and `hybridearn*` rows
  (moves between the spot and Earn wallets) are recognised non-events
  rather than "unhandled" types.
- Removed `taxjson show` (it printed `reports/NAME.sum`; use `cat`) and
  `taxjson verify` (Questrade-only live-positions check; `taxjson sanity`
  with `holdings = [...]` covers every broker and runs at the end of
  `taxjson run`).
- Docs refresh: README (website and REFERENCES links, the broker
  cross-check and the privacy gate under Verification, audit history,
  current test counts), a twelve-slide overview deck
  (`docs/deck/taxjson-deck.{html,pdf}`, rendered with WeasyPrint), and
  taxjson.com gains a commands-at-a-glance section.

## v0.15.1 (2026-09-18)

- `scripts/release.sh` accepts `vX.Y.Z` as well as `X.Y.Z`.
Pre-release audit (2026-09-18) of everything since v0.15.0 — two
independent adversarial reviews, every finding reproduced before it was
fixed:
- `scripts/check-pii.sh` failed OPEN in several ways: file names with an
  apostrophe or a space aborted `xargs` silently ("clean"); a malformed or
  CRLF denylist line disabled that entry; UTF-16 exports read as binary
  and were skipped; errors went to /dev/null. It now uses NUL-delimited
  file lists, validates every denylist regex, fails closed on scanner
  errors and on text files containing NUL bytes, scans file NAMES (IB
  names downloads after the account id) and — via the hook — commit
  messages, resolves ad-hoc paths before changing directory, and masks
  hits without interpreting the pattern.
- `scripts/hooks/pre-push` scanned `git log -p` of `remote..local`, which
  omits merge-commit resolutions and, when the remote tip is unknown
  locally, dies silently and lets the push through. It now scans the net
  `git diff remote local` (or everything not on the remote), plus the
  commit messages, and fails closed.
- `taxjson redact`: ids under five characters and date-like 8-digit
  numbers are no longer ids (small "ids" were rewriting quantities and
  prices); the integer part of a decimal is not an id; `_` is a word
  boundary; IB `DU`/`F` ids, Fidelity/Schwab-style `Z…`/hyphenated ids,
  `ClientAccountID`/`AccountAlias` columns, `Owner:`/`Customer:` rows and
  quoted `"Name: Last, First"` headers are recognised; the id in the
  FILE NAME is replaced too; UTF-16 and cp1252 exports are decoded (and
  noted) instead of passing through unredacted; CRLF, BOM and sibling
  cells' quoting are preserved; an invalid denylist/`--also` regex is a
  note, not a traceback; the report shows id lengths, never digits;
  an existing copy needs `--force`, a symlink is never written through.
- `taxjson shares`: an empty `--taxable`/`--sheltered` scope is an error,
  a non-table `[accounts]` entry no longer tracebacks, JSON quantities
  are rounded.
- `taxjson sanity`: malformed holdings files (non-numeric or non-finite
  quantities, a `[holding]` table) are clear errors; a `+` in a
  configured holdings path no longer breaks the config-driven pairing.
- `install.sh`: only exact `vX.Y.Z` tags are installable (an rc or
  four-part tag never ships), the body runs inside `main()` so a
  truncated download executes nothing, an existing non-symlink
  `~/.local/bin/taxjson` (pipx?) is refused rather than replaced, the
  install dir is canonicalised, and a failed run says re-running resumes.
  `dev-setup.sh` warns when the hook cannot be installed (worktree,
  `core.hooksPath`, an existing hook) and creates the denylist 0600.
- Scope: the US engine is labelled EXPERIMENTAL — README, the country
  table, a note printed by `taxjson init --country usa` and at the start
  of every US `run`. Its rules are implemented and unit-tested but have
  never been validated on a real account; Canada is the supported
  product. CONTRIBUTING gains a "Help wanted: broker exports" section.
- `taxjson redact FILE...`: strip account numbers and identity from
  broker exports while keeping every row shape — same-length placeholders
  (`U99900001`, `99900001`) applied consistently across the file,
  IB Account Information name/alias/address rows, `Name:`/`Client:`
  header lines, e-mail addresses, and the private denylist. Writes
  `NAME.redacted.EXT`, never touches the input, reports masked ids only.
  Verified on real IB, Questrade, RBC and Webull exports: the copies
  parse to identical tax objects apart from the account field.
- `taxjson shares`: the combined quantity held of each symbol across all
  accounts (post ticker.map, wash-adjusted where built) with a
  per-account breakdown and combined book cost; `--taxable` /
  `--sheltered` scope, `--options` to include contracts, `--sort qty`,
  `--json`.
- `scripts/check-pii.sh`: personal-data / secret scan — broker account-id
  shapes, home paths, unlisted e-mail addresses, credential-looking
  strings, and a private denylist kept outside the repository
  (`~/.config/taxjson/pii-denylist`, scaffolded by `dev-setup.sh`). Runs
  as a `ci.sh` stage in every mode and as the `pre-push` hook
  `dev-setup.sh` installs, which scans only the lines a push would
  publish and refuses on a hit. On a public repository a push is
  publication; this is the check that sits in front of it.
## v0.15.0 (2026-09-17)
- Gate: `scripts/ci.sh` runs with stdin detached (`exec </dev/null`).
  CLI tests spawn `taxjson run` subprocesses that inherit stdin, so a
  gate started from a terminal (e.g. by `scripts/release.sh`) stopped at
  a fixture's election prompt waiting on the keyboard, then recorded the
  default and failed the non-TTY deferral test. The pending-election
  tests also pass `stdin=DEVNULL` themselves.
- Tests: the two tests that drive pipeline stages in-process now capture
  their progress lines, so the gate prints only stage summaries — the
  leaked `==> margin wash-radar pass` lines named a synthetic fixture's
  account and read like a run on real books. docs/releasing.md states
  that the gate never reads a real project.


- Tests: the web-UI tests skip (not error) when `fastapi` is installed
  without starlette's test transport (`httpx2`, in the `[dev]` extra) —
  starlette raises `RuntimeError`, not `ImportError`, for that case.
  `scripts/dev-setup.sh` now installs `[web,fx,dev]` so a fresh clone
  runs the whole suite.
- Open-source release plumbing: a curl one-line installer (`install.sh`,
  latest release tag into `~/.local/share/taxjson`, `TAXJSON_CHANNEL=dev`
  tracks main), `scripts/release.sh` (dirty-tree/branch checks, CHANGELOG
  promotion, version bump, full gate, annotated tag, push),
  `scripts/check-consistency.sh` in every `ci.sh` mode, `docs/releasing.md`,
  `CODE_OF_CONDUCT.md`, a feature-request template, and `REFERENCES.md`
  mapping every engine rule (and every deliberate non-feature) to its
  ITA / CRA / IRC source. The old clone-then-run `install.sh` is now
  `scripts/dev-setup.sh`. Project home moves to github.com/taxjson/taxjson.
- KNOWN_ISSUES: two cited scope entries — IT-479R para 25 (option written
  in one year, exercised in the next) and non-eligible dividends in the
  estimate.
- Removed the Qt desktop app (`taxjson gui`, the `[gui]` extra, the
  `taxjson-gui` entry point, `packaging/`, and its tests). It
  duplicated the `sum`/`positions`/`harvest`/`estimate` tables behind
  a ~100MB dependency that stayed outside `[all]`, and its commit
  history was audit fixes rather than features. The local web UI
  (`taxjson serve`) stays. Recoverable from git history.
- IB parser: a `Ca` (cancellation) row in the Transfers section
  consumes its original leg (same symbol, date, opposite quantity,
  same type). IB lists a reversed ACATS/ATON leg as original + Ca +
  rebooked rows; a real RRSP move (IB -> Questrade) carried MDA five
  times — arithmetically -383, but the two Ca legs read as +383
  ACQUISITIONS to the superficial-loss walk and littered `taxjson
  events`/`transfers`. A Ca whose original sits in an earlier
  statement is kept as a reversing leg with a note.
- IB parser: per-fill Transaction Fees rows all fold into their Order
  row. IB levies UK Stamp Tax per fill while the Trades section
  carries one Order row, so a 10,000-share buy filled 8,900 + 1,100
  had two levy rows; the taken-once fold sent the second out as a
  standalone FEE that never reached the ACB (real 2025 AWE.L buy,
  5.43 GBP). Each trade now keeps an unlevied quantity so several
  rows can fold into it; two same-day trades with one levy each still
  pair 1:1.
- `taxjson.toml` accounts accept `holdings = [...]` — paths of the
  account's broker positions files (portoml-style). `taxjson sanity`
  with no arguments builds the paired groups from it (accounts that
  list a common file merge into one group; an account whose file is
  missing is noted and left out), explicit arguments still override,
  and `taxjson run` finishes with the same check as a WARNING — never
  a failing exit, since same-day trades not yet in the CSVs differ
  routinely. Only a compare against the broker catches a stranded
  position every internal report agrees on.
- Engine (Canada): the settle-straddle re-denomination now compares
  the trade's execution MOMENT (date and clock time) against the
  split's, not dates alone. IB posts corporate actions in an evening
  batch (20:25) dated the trade day, so a sale executed that morning
  and settling T+1 was pre-split — the date-only "strictly between"
  test skipped it, the ladder split the pool first, and a real FFN
  11-for-10 (2026-07-02) left 84 x 0.1 = 8.4 phantom shares with
  $83.81 of stranded cost in a sheltered account. Found by `taxjson
  sanity` against the broker's positions; taxable books unchanged.
  The straddle fuzzer gains an "evening" placement for this shape.
- `taxjson sanity` matches an option row through its `underlying`
  field when the file spells the option root differently from
  taxjson (IB names the Montréal contract on RCI.B by the underlying,
  a positions export by the exchange root `RCI`); the fallback fires
  only when the file's spelling has no taxjson counterpart, is noted
  in text mode, and listed as `matched_via_underlying` in JSON.
- `taxjson sanity` gains a PAIRED form alongside the aggregate one:
  `ACCOUNT[+ACCOUNT]=FILE[+FILE]` checks only those accounts against
  only those holdings files, as its own group (a repeated left-hand
  side merges: `margin=ibkr.toml margin=webull.toml`). Many-to-many
  because a taxjson account can span several broker accounts and one
  broker export can cover several accounts. The aggregate form is
  blind to a position booked in the wrong account (the totals still
  agree); the paired form flags it per group. Both forms mix freely;
  an account or file placed in two groups is a usage error. JSON gains
  a `groups` list; text mode adds an ACCOUNTS column when paired.
- `taxjson scan` prints a root-aware MAP-UNUSED note for ticker.map
  rules that match no parsed symbol — counting OPTION roots (a rule
  with no stock rows is still live when option trades carry its root,
  since identical-property matching folds the option's underlying
  through it) and suffix-less codes. A root-blind dead-rule check had
  pruned ten live TOBASE/GLOBAL rules from a real map, splitting every
  affected option's identity class (the DFDV1 assignment legs stopped
  netting against the DFDV short puts). A note only: it never fails
  the scan.

Deep audit, round eight (2026-09-14/15): a privacy/secrets sweep of
the tree AND the full git history ahead of going public, a parser
coverage audit that classified every row category in a full set of
real broker exports (IBKR Flex, Questrade, RBC, Webull, Kraken,
Coinbase), and an adversarial pass over the last cold surfaces (GUI,
web what-if, watch/diff/reconcile-slips/distributions, orchestrator
edge cases). Suite at 2,118 tests.

Parser coverage (real rows the parsers dropped or mis-booked):
- Kraken stablecoin (USDC/USDT/DAI) staking rewards were valued at $0
  income: the asset was folded to `USD` before the reward row was
  built and the price filler skipped `USD`. Priced at 1.0/unit with
  income = quantity; no phantom stablecoin position is opened.
- IBKR option EXERCISE (`Ex` code) was booked as a plain BUYSELL at
  price 0 — the premium realized as an option loss instead of rolling
  into the stock leg's basis/proceeds as an assignment does. Now an
  ASSIGN leg; verified end-to-end in both engines.
- IBKR `Trades / Forex` conversions (hundreds per year) fell through
  the asset filter with no accounting — now counted as recognized
  non-events with a KNOWN_ISSUES entry (FX conversions are not
  modeled as dispositions). `Transaction Fees` (UK stamp duty) fold
  into the same-day trade's fee (else a symbol-bound FEE row);
  `Commission Adjustments` (refunds) become negative FEE rows; tender
  / voluntary-offer corporate actions are recognized (zero-proceeds
  round trips are no-ops, cash allocations surface loudly instead of
  vanishing). IB `Fees` section sign corrected (charges are positive
  FEE amounts, matching every other emitter — a market-data fee had
  registered as a cash INFLOW in fx-cash).
- Kraken: `transfer/transferpeertopeer` outbound stablecoin is custody
  evidence (send NOTE fires); fiat-base fills and fiat-fiat dust sweeps
  no longer create phantom `USD`/`CAD` assets.
- Questrade: `REI` dividend-reinvestment rows are purchases at the
  reinvest price (DRIP shares never entered inventory); `CIL` cash in
  lieu of a fractional stock-dividend share is booked as a same-day
  fraction buy/sell pair; `FCH` ADR custody fees become FEE rows;
  `FXT` conversions are labelled non-events.
- Row accounting everywhere: the IB parser now reconciles under
  `--lint` (every row consumed or counted); recognized non-events
  (subtotals, metadata sections, deposits, staking-wallet shuffles)
  report in a calmer note than genuinely unclassified rows; subtotal
  rows no longer trigger false "dateless fee" or "currency ''"
  warnings; a transfer-only file no longer trips the
  "parsed to 0 transactions" alarm.

Cold surfaces (round eight):
- `run --account X` printed "filed YEAR: OK" against artifacts it had
  just declared stale (wash pass skipped); now "not checked" under
  `--account`, and `sheltered_base.json` is rebuilt when the named
  account is sheltered.
- GUI: the summary shows TAXABLE / SHELTERED subtotals and the tax
  pane's "Realized (taxable)" comes from the taxable subtotal (both
  silently included sheltered accounts); quantity columns use the
  quantity formatter (crypto positions read 0.00).
- `taxjson-apply-distributions` keys the record-date balance on
  SETTLEMENT for settle-basis projects (`--date-basis`; the wrapper
  passes the project's `tax_date`) — an unsettled sale still holds on
  the record date, a buy traded on the record date does not.
- Web: mixed-currency holdings render per-currency costs with a MIXED
  marker (cells were blank); a what-if loss in a sheltered account is
  labelled "not deductible (registered account)".
- `reconcile-slips` excludes crypto accounts (exchanges issue no
  T5008/1099-B, so it could never exit 0); `taxjson-diff` reports a
  missing/malformed input cleanly; `TAXJSON_OFFLINE` now also covers
  the price chain (harvest, watch --harvest, GUI harvest).

Privacy sweep (pre-publication): the working tree carried no secrets
but many real-portfolio fixtures and samples; all replaced with
synthetic equivalents (merger book, RBC option, custody declaration,
README sample tables, dividend-snap examples, fuzz-doc paths). The
git HISTORY still holds real account numbers and disclosing commit
messages — see the release checklist: squash before going public.

- Questrade DRIP (`REI` / "Dividend reinvestment") rows are booked as
  purchases at the reinvestment price (`REINV@C$8.33966` in the
  description; Price column is 0) — the DRIP shares never entered
  inventory before (counted skip). The cash dividend keeps its own
  Dividends row. Questrade `CIL` (cash in lieu of a fractional
  stock-dividend share) is booked as a same-day pair: the fraction at
  its $0 stock-dividend cost, then sold for the cash.

- `scripts/ci.sh` — the authoritative local CI gate (GitHub Actions
  is billing-gated on this private repo): ruff critical tier + the
  full suite + the three property fuzzers at CI depth; `--nightly`
  for 5000/3000/1600 books, `--mutation` to append the mutation
  harness, `--quick` for lint + suite. Appends one line per run to
  `.ci/history.log`. The Actions workflow is slimmed to Linux only
  (macOS runners cost 10x minutes) and gains manual dispatch and a
  nightly deep-fuzz job for whenever Actions is enabled.
- `scripts/mutation_audit.py` refuses to run without `--yes` and
  offers `--list-targets` — a bare invocation used to start the
  hours-long in-place mutation run immediately.
- Docs: README gains a Verification section (audit authority,
  fuzzers, mutation testing, audit rounds, filed-year locks);
  CONTRIBUTING documents the gate, the fuzzers' depth env vars, and
  the mutation harness's in-place hazard; KNOWN_ISSUES header
  updated to seven audit cycles.

## v0.14.0 (2026-09-11)

Deep audit, round seven (2026-09-10): four parallel audits over the
COLD surfaces — the US engine on its own terms, corporate actions +
elections + the forward-looking advisory tools, the security/I/O
boundary, and the stage tools + FX conversion — plus a cross-tool
reconciliation of every filing command against the audit on real
books (agree per symbol to the cent). 27 confirmed defects fixed and
pinned; suite at 2,077 tests.

US engine (wrong numbers):
- Holding period follows Rev. Rul. 66-7: an end-of-month acquisition
  is long-term from the 1st of that month a year later (Feb 29 ->
  Mar 1, not Mar 2; Feb 28 common-year -> Mar 1). The old rule — and
  its pin — had both directions wrong. `held_more_than_one_year` is
  now module-level and `harvest`'s "LT IN" date derives from it
  (was a fixed +366 days, one day early a quarter of the time).
- §1223(3) tacking is per-share: a replacement lot larger than the
  match is split at the match point (in the match loop, or at lot
  creation when the loss preceded the buy) so only the matched shares
  carry the tacked period and the §1091(d) bump. A 200-share
  replacement for a 100-share loss now terms 100 LT + 100 ST.
- Same-timestamp assignment legs: the OPTION leg now sorts before
  the STOCK leg in both tie-break ladders — stock-first input order
  silently dropped the premium (proceeds 10,000 instead of 10,300).
- Documented as out-of-scope with statutory cites: §1091(e)(1)
  long-sale trigger on short-cover losses, the IRA-vs-taxable
  already-sold-replacement asymmetry, advisory-only option
  replacements, FIFO-only (no specific-lot ID).

Corporate actions / elections (money):
- Questrade s.86.1 spinoff: the ACB-reduction ADJUST landed on the
  broker's internal SEC# instead of the parent ticker, leaving the
  parent at full ACB while the spun shares also carried basis
  (double count). The parent ticker is resolved from the statement;
  saved elections keyed under the old id are migrated.
- `elect --set` validates election name and hints against the
  event's action type on EVERY path (re-election after `--reset`
  used to save an invalid election and crash the next run with a
  raw KeyError); the "matches no pending event" warning fires only
  when neither source knows the id.
- IB reverse split with cash-in-lieu: the fractional share's
  disposition is booked at the cash and the pool ends at whole
  shares (was 3.3333 dust, CIL row dropped). IB's exact CIL wording
  is unverified — KNOWN_ISSUES entry.

Advisory tools (a wrong deadline costs the loss permanently):
- The wash-radar VIOLATION rescue deadline was the SETTLEMENT bound;
  trading on it settles a day late and the engine denies the loss.
  The radar, sell-check, and the JSON sidecar now carry the last
  safe TRADE date (T+1 rule, weekends skipped) with the settle
  deadline alongside; new `lib/dates.py` is the single settlement
  helper.
- Radar/sell-check/buy-check match on the engine's rename classes
  (SPLIT symbol_new): a loss on OLD.TO followed by a rebuy of NEW.TO
  showed COOLING/EXITABLE instead of VIOLATION.
- buy-check/sell-check honor `DISTINCT` (a CDR pair is no longer
  treated as identical property); a bare root shared by kept-apart
  members reports the worst verdict with an ambiguity note.
- harvest buckets BLOCKED losses as claimable NOW (a further loss
  sale today is clean; only a rebuy is the risk) — `watch
  --harvest` follows. safe-to-sell counts ASSIGN acquisitions; the
  web what-if renders a passed VIOLATION deadline as "loss denied".

Stage tools / FX (silent wrong money and crashes):
- A lowercase or space-padded currency code (`cad `) under `--to
  CAD` was multiplied by the USD default rate and relabelled CAD.
  Codes normalize on both sides; a currency wholly absent from a
  supplied rates file is now FATAL unless `--default-rate` is
  explicit — `taxjson run` users with an unlisted currency get a
  message naming `source_currencies` instead of a silent 1.35x.
- Rates-file parser: malformed lines counted and reported,
  NaN/Infinity/non-positive rates refused, currency columns
  upper-cased.
- `coerce_transaction_row` is the single type funnel: null/wrong-
  typed numerics and non-string symbol/account/date are refused
  with a row-level error naming the field (were bare tracebacks in
  loaders, phantom walks, and the engines; `taxjson-validate`
  itself crashed on wrong types and passed `null` numerics).
- merge2 / convert-currency / sort / fill-crypto all route through
  the shared loader (comment stripping, qty alias); a per-file read
  error no longer yields an EMPTY merge at exit 0.
- ticker.map DELETE-then-GLOBAL order is the same in merge2 and the
  standalone tool (equity vs crypto accounts read one map
  differently).
- `taxjson-sort --dedup` never drops rows (shared validator; its
  warnings now surface in the .sum); `.tt` books get per-file fill
  disambiguation so two identical hand-entered lines no longer
  collapse under dedup; fill-crypto's cache keys on the resolved
  Yahoo id and leaves qty=0 rows untouched.

Security / I/O boundary (critical surfaces verified clean: token
handling, subprocess argv, web server, input parsing):
- Account names are validated (`../x` wrote artifacts OUTSIDE the
  project; `-x` was parsed as a flag). Project directories are
  created 0700; the IBKR Flex download is atomic.
- Credentialed broker requests refuse redirects (CPython forwards
  the bearer across 30x hops) and non-HTTPS API servers.
- `TAXJSON_OFFLINE=1` forbids the two default egress paths (FX
  rates, un-priced crypto rows) — documented in SECURITY.md with
  the full network/egress list; the web UI no longer loads Swagger
  from a CDN; an oversized CSV field is a clear error instead of a
  `_csv.Error` traceback.

Full audit, round six (2026-09-09): adversarial review + rigor sweep
(5,000/3,000-seed fuzzers clean; a NEW settle-straddle differential
fuzzer found one genuine engine bug — promoted into the suite as
tests/test_settle_straddle_fuzz.py, default 200 seeds,
TAXJSON_STRADDLE_FUZZ_BOOKS scales) + hand-verified 2026 estimate
math (60 checks to the cent). Suite at 2,019 tests.

Engine (wrong numbers):
- The per-account balance walk feeding the cover-vs-opening trigger
  gate sorted WITHOUT the phase ladder: a sale settling exactly on a
  split date was subtracted from an already-scaled balance, creating
  phantom shares that both fabricated spurious denials and masked
  real triggers (settle-straddle fuzzer, seeds 113/317/433/781/1433;
  minimal 7-row repro pinned). Now phase-aware like every sibling
  walk; all 1,600 fuzzer seeds hold.
- The straddle re-denomination window keys on the split's SORT date
  (settle when set): a settle-desynced SPLIT row no longer
  re-denominates a trade the pool splits after (was silent phantom
  shares — the pre-eb6a0a3 refusal had caught it).
- HIGH regression fixed: `_replay_moves_on_base` folded evidence-move
  endpoints through the to-base renames, but the RAW-base holdings
  inventory is PER-LISTING (GLOBAL-only) — every TOBASE-pair depot
  flip was skipped and the flipped shares' base cost vanished from
  holdings.toml. Replays now fold through the JOURNAL set only
  (matching the base build); the mispremised pin corrected; the
  renderer's cross-listing base fallback retired.

Detector/evidence hardening:
- The restatement pre-pass disqualifies segments with MAIN-book
  SPLITs in-span (was: a guarded symbol could raise the event count
  and launder its siblings past the near-trade refusal); null-date
  guards added at both scan sites.
- Kraken fiat (USD/CAD/EUR/GBP) withdrawals/deposits are back to the
  counted note — moving your own cash is not custody evidence, and
  the FMV-disposition note was wrong tax advice for it. Stablecoins
  stay in the evidence branch. Coinbase evidence rows normalize the
  symbol, carry the CSV's price currency, and raise on unparseable
  timestamps like every other dated row.

Estimate/ACQUIRED polish:
- `apply_vintage` resets to the default vintage on a missing year
  (was sticky in-process); pre-earliest years documented as using the
  earliest table. Result vintage labels pinned against the applied
  tables; a 2026 US bracket edge pinned.
- ACQUIRED: comma quantities parse; the counter-TRANSFER is emitted
  at the fixed 09:30:00 default so a hand-written pair and an
  expansion hash to identical ids; qty 0 refuses; the Canada error
  prescribes the one-liner only for LONG-direction rewrites (a short
  can't be expressed by ACQUIRED) and the US error mentions it.
- Docs reconciled: SplitStraddlesSettlementError docstring (refusal
  is rename-split-only now), dead contradictory comment removed,
  `taxjson transfers` help/README mention crypto sends, README
  estimate sample refreshed to the Bill C-4 figures, issuer-match
  docstring aligned with the one-extra-token rule.

- 2026 rate tables, vintage-aware: `apply_vintage(year)` selects the
  published table for the project year (latest-at-or-before for
  future years; the printed vintage discloses which). 2026 figures
  verified against the CRA indexation release (x1.02, Bill C-4 14%
  full-year: brackets 58,523/117,045/181,440/258,482, BPA 16,452),
  ON x1.019 (surtax 5,818/7,446, BPA 12,989; 150k/220k bands
  unindexed), BC Budget 2026 (lowest rate 5.06% -> 5.60%, thresholds
  x1.022), AB Bill 32 (x1.02, 8% first bracket now indexed), and IRS
  Rev. Proc. 2025-32 + OBBBA (std deduction 16,100; indexed ordinary
  and LTCG brackets). The 2025 AB 14->15% boundary corrected
  (302,757 -> 362,961). VERIFY notes retained in-code.
- Settle-straddle re-denomination: a SPLIT dated strictly inside a
  trade's settle lag no longer refuses the book — the executed
  quantity/price are re-denominated through the split (qty x ratio,
  price / ratio, money untouched) and booked correctly against the
  post-split pool, with a NOTE. The loud refusal remains only for
  RENAME-splits straddling the lag (re-symboling mid-flight). The
  06-11/06-12/06-13 golden family now all produce the identical $500
  denial.
- `ACQUIRED` .tt sugar: `ACQUIRED <true-date> <time> <sym> <qty>
  <cur> <price> <total> ARRIVED <arrival-date>` — one line for the
  lost-history custody idiom, expanding to the BUYSELL at true
  cost/date plus the DECLARED counter-TRANSFER netting the arrival
  leg. The AmbiguousTransferDateError message prescribes it.

- Crypto withdrawals/sends become custody EVIDENCE (KNOWN_ISSUES
  graduation): Kraken ledger withdrawal/deposit rows and Coinbase
  Send/Receive rows emit TRANSFER rows into the per-broker sidecar —
  `taxjson transfers crypto` shows the full custody history, matched
  send/arrival pairs read as self-custody moves at a glance, and
  unmatched out-legs are the gift/payment candidates. The parse
  prints the FMV note for out-legs (a send that left your ownership
  is a taxable disposition at fair market value — declare a .tt sell;
  Coinbase rows carry the spot price so it's copy-paste). Stablecoin
  evidence keeps its own name (a USDC gift disposes USDC the
  property) though trade books still fold USDC/USDT/DAI to USD for
  pricing; Kraken Earn shuffles stay ignored. Books, gains, and
  `events` unchanged — evidence, not events.
- The transfer-aware differential fuzzer is promoted into the suite
  (tests/test_transfer_fuzz.py): eight generated churn shapes
  (custody pairs, restatement clusters, DECLARED legs near trades,
  chained-gap residue) checked for crashes, conservation, sign
  sanity, and determinism. Default 150 seeds per run;
  TAXJSON_TRANSFER_FUZZ_BOOKS scales it (round-five ran 2,400 clean).

- Derived prices are stored repr-clean: every parser price computed
  by DIVISION (IB corporate-action and transfer branches, Kraken
  trade legs, the crypto price filler) now rounds to 8 decimals
  before storage — `taxjson events` printed the raw double noise
  (`25.810000000000002`) because the round-trippable taxtext views
  deliberately display the EXACT stored value. 8dp kills the noise
  while keeping satoshi-level crypto prices intact; Questrade
  already rounded. The `transfers` view quantities go through
  `fmt_qty` (no scientific notation on large counts).

- Report tables right-align all-numeric columns (money, counts,
  percentages; '-' placeholders tolerated), lining up decimal places
  and cents down every column — `taxjson sum` and every other
  `format_report_table` view, plus the per-transaction views
  (`events`/`divs`/`trades`/`gains`/`fees`/`roc`/`leaps`: quantities,
  per-share rates, and amounts align; the rows stay paste-into-.tt
  parseable). Free-text columns stay left-aligned; detection is
  per-table, so a column with any text cell is left alone. The .sum
  tables, carryover/t1135/form-export, fx-cash, instalments, harvest,
  the GUI, and the web UI already right-aligned.

## v0.13.0 (2026-09-08)

Full audit, round five (2026-09-08): complete-codebase pass — four
parallel audits (adversarial review of everything since v0.12.0, an
engine-rigor re-run, a filing/money-layer audit with hand-computed
CRA/IRS scenarios, and a repo-wide consistency sweep). Engine core
held: 5,000-book conservation sweep clean, and a new transfer-aware
differential fuzzer (2,400 seeds over custody churn, restatements,
declared legs) found zero violations. Suite at 2,001 tests.

Restatement detector hardened (adversarial findings):
- Three discriminators keep genuine tax events out of the event pool:
  a qualifying cluster's first non-declared leg must be an OUT-leg
  (restatements journal out-and-back; contributions are in-first); a
  detected event's total span is capped at 7 days (pad-chaining can
  no longer glue unrelated pairs weeks apart into a symbol count);
  and guarded segments (in-span trade/SPLIT) never raise the count.
- Segments carrying DECLARED legs keep the attestation bless-pad path
  even inside a detected event — the event bypass no longer widens a
  declaration's reach onto gap-chained genuine legs.
- The real 40-symbol Aug-2026 event still detects; the misclassifying
  shapes (3 in-first contribution pairs; week-spaced pair chains) are
  refused. All pinned.

Evidence-driven depot flips hardened:
- Evidence nets are computed on journal-FOLDED keys: a JOURNAL pair's
  legs cancel, so intrinsically-fungible classes are never
  evidence-moved on top of their fold (previously re-symboled shares
  already sold through the other listing).
- The base inventory now REPLAYS the native pass's applied moves
  (endpoints mapped through the to-base fold; same-key moves no-op)
  instead of re-deriving them — the two inventories can no longer
  diverge. Renderer falls back through the fold for base lookups.
- A transfers=false→true toggle unlinks the stale sidecar (was
  consumed alongside the now-in-book rows: double counting).
- 17 mutation-gap pins landed (partial-flip pro-rata apportionment,
  prune/no-op guards, bless-pad and chain-pad boundaries, journal
  fold, relative tolerance, unordered input).

Filing layer (money findings):
- Rate tables: lowest federal rate corrected to Bill C-4's 2025
  blended 14.5% (also reprices BPA and AMT-BPA credits); US single
  standard deduction to OBBBA's 15,750. Both marked VERIFY in-code.
- Filed-year locks snapshot proceeds and US ST/LT subtotals — the
  two audit-proven check-filed blind spots (gain-preserving
  proceeds/ACB shifts, term flips) now drift loudly; pre-upgrade
  locks stay valid.
- `taxjson sum`: the tainted warning was dead code for
  pipeline-written files (rows are stripped to
  manual_reporting_required, so the in-line count was always 0) and
  its text claimed totals INCLUDE what they exclude. Both homes now
  counted separately with accurate warnings; JSON carries
  `tainted_routed`.
- KNOWN_ISSUES gains the T1135 transfer-in cost caveat.

Scan/CLI/consistency:
- `_issuer_names_match` prefix rule tightened to one extra token
  (Brookfield Asset Management vs ...Reinsurance Partners no longer
  match); the name-cap note states honestly that only the first 80
  symbols are probed; `DISTINCT X X` warns.
- `taxjson harvest --options` actually forwards (it was defined but
  never read — the GUI's include-options toggle was silently
  ignored); `taxjson audit --no-color` exists through the wrapper.
- Dead code removed (unused imports across seven modules,
  `_cont_wrap`, `fetch_price_histories`); committed tmp debris
  purged; doc headers agree on all five ticker.map verbs; README
  documents `taxjson transfers`, the sidecar, CDR-PAIR/MAP-BAD?, and
  the evidence-flip exception; CHANGELOG gained its v0.12.0 heading.

- Evidence-driven depot flips: the holdings view now re-symbols ONLY
  the quantities the transfer sidecar PROVES were journaled between a
  security's listings (matched net residuals within a map identity
  class, capped at held quantity; shares never created or destroyed —
  a flip that was flipped back nets to zero and moves nothing, lone
  migration legs are ignored). `JOURNAL` map lines are for
  intrinsically fungible classes (DLR's gambit units) — an ordinary
  cross-listing stays `TOBASE` and its holdings only merge when the
  broker's own InterDepot rows say so.

- Custody-transfer sidecar + `taxjson transfers` view: a taxable
  book's TRANSFER rows are deliberately not tax events (basis comes
  from the buy/sell history) — but the parse stage silently DELETED
  them, leaving no way to discover a depot flip, listing journal, or
  broker migration later (the OR.US/OR.TO mystery: IBKR's InterDepot
  row existed in the CSV all along). Excluded rows now land in a
  per-broker sidecar (`work/<acct>_<broker>_transfers.json`) with a
  parse-time note, and the new `taxjson transfers [ACCOUNT]` view
  shows them (plus in-book TRANSFERs from `transfers = true`
  accounts) with the broker's transfer type (InterDepot / Internal /
  ATON). Books, gains, and `events` are unchanged — evidence, not
  events.

- Account-wide restatement detection: when >= 3 symbols in ONE
  sheltered account share zero-net TRANSFER clusters over a common
  few-day envelope, the pipeline classifies the whole thing as one
  broker custody event and nets it — no per-symbol DECLARED
  attestation needed (the evidence is in the data: no tax event
  journals an account's inventory out-and-back to zero). One NOTE
  names the event. Below the threshold the per-symbol near-trade
  refusal and the DECLARED resolution path still apply.
- Unmapped cross-listing journal candidates: an out-leg of X pairing
  with an in-leg of a DIFFERENT symbol Y (same account, equal qty,
  within 7 days) is the fingerprint of a dual-listing journal the
  ticker map doesn't know — mapped pairs were normalized to one
  symbol before netting ran. Surfaced as a `TOBASE X Y` suggestion
  instead of silently feeding the loss walk a disposal + acquisition.
- CDR awareness + the DISTINCT map verb: Canadian Depositary
  Receipts (UNH.TO over UNH.US) name the SAME issuer but are NOT
  listing equivalents — fractional, CAD-hedged, floating ratio. The
  scan detects them from the exchange shortName (the longName is the
  clean issuer name), never suggests mapping one (CDR-PAIR says so;
  unheld CDR twins are silently skipped), and flags a map entry
  pairing a CDR with its underlying as MAP-BAD?. New ticker.map verb
  `DISTINCT a b` records that two look-alike listings are
  deliberately separate securities (a CDR, or same-root different
  companies like EFX.TO Enerflex vs EFX.US Equifax) — changes no
  symbol, silences the MAP-GAP nag.
- `taxjson scan --online` map robustness: clusters HELD listings by
  exchange-reported issuer name to catch DIFFERENT-root dual listings
  (BTG.US/BTO.TO — same-root scanning can never see these), and
  verifies every defined GLOBAL/TOBASE/JOURNAL pair names one issuer
  (`MAP-BAD?` on mismatch — a typo'd pair merges two companies' ACB
  pools). Conservative matching; findings say VERIFY.

## v0.12.0 (2026-09-06)

Full audit, round four (2026-09-06): adversarial review of the
round-three fixes, an engine-rigor re-run (800-book fuzz sweep passed;
an extended 5,000-book sweep and a 132-mutant audit found the items
below), and a release-readiness sweep.

Engine (wrong numbers):
- Cross-pool deferral routing (fuzz seed 2183): when a superficial
  loss's still-held backing sat entirely in a SIBLING pool of the
  alias class (loss on S0, substituted property in S2, united by a
  later rename-merge), the s.53(1)(f) ADJUST landed on the trigger's
  own — empty — pool and the denied loss silently vanished from
  conservation. Bumps now land on a pool that actually holds backing
  at +30 (trigger's own pool preferred, greedy otherwise).
- A SPLIT dated exactly on a settle-lagged loss sale's settlement date
  doubled the denial (the ref side of the lineage conversion treated
  the split as already baked in); `lineage_factor` gains a
  `ref_inclusive` knob driven by the loss row's own settle lag.
- A SPLIT dated STRICTLY between a trade's execution and settlement
  silently booked the pre-split-denominated quantity against the
  post-split pool (a $2,000 loss became a $3,000 "gain" with phantom
  shares). New `SplitStraddlesSettlementError` refuses the book
  loudly with re-dating instructions. US engine (trade-date ordering)
  unaffected and pinned so.
- Default fuzz depth raised 25 → 200 books (mutation testing showed
  10 surviving engine mutants the existing invariants kill at 200).

Transfer declarations hardened (round-three follow-through):
- The declaration marker is now an OPT-IN `.tt` token — `TRANSFER ...
  DECLARED` — instead of every hand-written TRANSFER auto-attesting:
  .tt-only books record genuine in-kind moves as TRANSFERs, and a
  json→tt→json round trip must never grant broker rows attestation.
  The emitter round-trips the token; undeclared `.tt` TRANSFER ids
  stop churning across versions.
- A DECLARED leg's blessing now reaches only rows within 7 days of a
  declared leg — a declared June pair no longer silently nets a
  genuine July contribution that gap-chained into the same segment.
  An attestation that doesn't net to zero within its own reach nets
  nothing and says so.
- Marker check is exact equality (a broker description merely
  containing the marker text no longer attests); engine error
  messages and the refusal NOTE prescribe the DECLARED form.

Reporting/CLI:
- `taxjson sum --json` totals now come from the same 2dp row-sum path
  as the printed tables and `subtotals`, so totals == Σ subtotals
  exactly for machine consumers.
- `taxjson-gui --help` prints usage and exits instead of opening a
  window (and works without PySide6).
- README: grouped `sum` tables documented; new section on sheltered
  transfers, `AmbiguousTransferDateError`, and the DECLARED
  declaration/attestation flow.

- `taxjson sum` groups the summary into TAXABLE ACCOUNTS / SHELTERED
  ACCOUNTS / ALL ACCOUNTS tables (each with its own subtotal) when
  taxjson.toml declares both types; accounts missing a type group as
  UNTYPED rather than joining either bucket. Single-type projects and
  single-account views keep the one-table layout. Every table's
  total row is the exact sum of its displayed rows. JSON gains a
  per-account `type` and a `subtotals` object.

Full audit, round three (2026-09-04): four parallel audits (engine
adversarial re-review, pipeline/orchestrator, parsers/importers,
web/GUI/reporting) over the round-two fixes. 28 confirmed findings,
all fixed and pinned; suite at 1921 tests.

Engine (wrong numbers):
- Canada's superficial-loss trigger walk converted acquired-in-window
  and allocation-cap quantities with the CLASS-WIDE alias factor,
  which is wrong when a rename merges INTO a live symbol whose own
  shares never split. `SplitTimeline.lineage_factor` now simulates
  each raw symbol's forward lineage path and converts per-row
  (`_row_loss_units`); falls back to the class factor only when
  lineages don't converge. Fuzzer generates merge-into-live renames;
  three pre-fix-failing goldens pinned.
- Netter bypass trio: the per-account transfer dropper had no span
  cap (gap-chained Feb..Dec segments netted), no main-book
  visibility (same-account pairs inside a loss window silently
  erased), and ran before the cross-account netter could refuse
  chained rrsp→rrsp2→out hops. Segments now cap at 45 days, and the
  sheltered-side invocation refuses zero-net segments near any
  main-book trade of the symbol (trade or settle date, either sign)
  or containing a SPLIT.
- Hand-written `.tt` TRANSFER rows are stamped as manual
  declarations, and a zero-net segment containing a declared leg
  bypasses that near-trade refusal — the declaration is exactly what
  the `AmbiguousTransferDateError` resolution prescribes, so the
  guard must not re-refuse it. A cluster that already nets to zero
  but is refused for nearness prints the attestation form (a
  declared zero-net `.tt` pair) instead of the counter-TRANSFER
  prescription that would unbalance it.
- `taxjson fees` filtered by settle date while every gains window
  runs on trade date; a year-end trade settling in January silently
  fell out of the year. Trade-date basis now (falls back to settle
  only when trade date is absent).

Pipeline/orchestrator:
- The work-cache cleanup glob deleted PREFIX-SIBLING accounts'
  artifacts (`margin` cleanup removing `margin2_manifest.json`);
  scoped by exact-prefix segment match, and `_manifest.json` /
  `_mapped.json` / `_blend.diag` are never cleanup targets.
- Crypto stage order was sorted→filled→mapped, so ticker-map
  GLOBAL/DELETE rules never saw the symbols fill-crypto-prices
  needed; reordered sorted→mapped→filled→base.
- The atomic stage wrapper published its diag capture under a name
  the strict gate never read (`.stage.diag`); now lands as
  `base_json.diag` and the gate fails on it.
- `ccd-sum`/`winners` warn when the resolved gains artifacts don't
  cover the requested period token; leaps balances are per-account
  (cross-account contract counts no longer blend), and partial
  covers leave the opening remainder in place.

Parsers/importers:
- Coinbase Converts booked the buy leg at subtotal, dropping the fee
  from basis; now subtotal + fee (matches the Kraken convention).
- Generic importer reads `(x)` accounting negatives.
- Kraken orphan spend/receive ledger legs and bare `trade` rows warn
  and count as skips instead of vanishing.
- RBC NON-RES TAX rows derive qty/rate from the GROSS figure instead
  of the withheld net.

Web/GUI/reporting:
- What-if labeled multi-lot sells by the FIRST lot only: wash flag is
  now any-lot, term is `MIXED (LONG_TERM x, SHORT_TERM y)` when lots
  straddle.
- Wash-radar serves the COMBINED cross-account report; stale-window
  advisories say when the window has cleared since generation.
- GUI estimate renders the AMT block (top-up + TOTAL WITH AMT) it
  previously dropped.
- Holdings pages state their basis is the native per-account
  snapshot, not the s.47 filing ACB.

- `taxjson audit` output redesigned for legibility: a fixed label
  gutter (SOURCE / MAPPING / FX / DISPOSITION / WASH / TIE-OUT /
  TRACE) with dim/bold weighting and tty-aware color (auto-on for
  terminals, `--no-color` and NO_COLOR honored); parsed rows read as
  verbs (`SELL 20 CLS.US @ 284.99`) instead of raw
  `BUYSELL -20`; cross-checks are \u2713/\u2717 marks instead of
  `== ties`; figures right-aligned; the redundant raw-gain line
  dropped on non-wash events; prose (permanent-denial explanations,
  warnings) wraps inside the 86-column frame; the reconciliation
  footer aligns and carries per-line marks; proceeds and cost basis carry the per-share figure (`(20 sh @ 426.1292)`) for statement sanity-checks. JSON output unchanged.

Full audit, round two (2026-09-04): four parallel audits over the
layers never previously deep-audited — reporting/filing, pipeline
orchestration, cross-tool contracts — plus an adversarial review of
the v0.11.0 engine changes. 32 confirmed findings, all fixed and
pinned.

Engine (wrong numbers):
- Canada's four wash balance walks scaled the ENTIRE alias class by a
  rename-split's ratio, fabricating still-held balance (denied losses
  on fully-exited positions; conservation broken). All four walks now
  track per-raw-symbol sub-balances, scaling and folding only the
  symbol a SPLIT names — the US engine's model. The fuzzer now
  GENERATES rename-splits, which immediately caught:
- US engine: the rename-migration FIFO re-sort keyed on the
  §1223-tacked `effective_acq_date` — but tacking adjusts holding
  period only; Reg. 1.1012-1(c) FIFO goes by actual acquisition
  order. Wash and no-wash runs consumed different shares after a
  rename-split. Sorts by actual date now; conservation pinned.
- The rename-merge dropped parked `pending_wash` dollars (denied
  loss's future recovery silently erased); they now ride the rename,
  signed by the target pool's direction.
- Cross-account sheltered netting refuses segments containing a SPLIT
  or sitting within 30 days of a taxable sale of the symbol — a
  contribution + withdrawal pair is byte-identical to a custody move,
  and near a loss window the engine's AmbiguousTransferDateError now
  forces the declaration instead of silently allowing the loss.
- AmbiguousTransferDateError is handled cleanly by every consumer
  (explain, audit, carryover, the web what-if) instead of only
  taxjson-gains.

Reporting/filing (wrong numbers):
- work/<acct>_report.json income was ALWAYS zero (field-name
  mismatch); it now summarizes the base book like the .sum does.
- reconcile-slips mis-rendered SHORT dispositions (missing the
  proceeds/cost swap form-export performs) — false MISMATCH equal to
  the gain; and its tainted-row plumbing was dead (the pipeline moves
  tainted rows to manual_reporting_required) — phantom-basis sales
  now get the designed "tainted" note instead of
  MISSING_FROM_COMPUTED.
- check-filed recomputed with the wrong phantoms.json path
  (work/ instead of the project root): guaranteed false DRIFT on
  phantom projects, inviting an erroneous close-year --force.
- carryover now computes on the FILING basis: US per-account FIFO
  lots, and US crypto in a separate no-wash pass (the ledger used to
  deny a loss the return legitimately claims).
- Schedule 3's note distinguishes permanently denied superficial
  losses (registered-account acquisition — NO ACB addition) from
  deferred ones; the old single note told users to bump ACB for both.
- resolve_gains_files warns loudly when the preferred wash file is
  older than the plain gains file (run --account staleness reaching
  form-export/t1135/reconcile-slips silently).

Pipeline orchestration:
- The corp-actions stage joins the sources-manifest dependency —
  deleting a CSV under --fast no longer leaves its corp rows in the
  books.
- merge2 + apply-distributions publish ATOMICALLY: a failed apply can
  no longer leave a fresh-but-unadjusted base that --fast trusts
  forever (silently vanished ROC ADJUSTs).
- Renamed/removed accounts: `taxjson run` warns that orphaned work/
  artifacts are still counted (a renamed account was silently counted
  twice by sum/fees); parsed artifacts of REMOVED broker groups are
  cleaned up (stale .diag banners, dead fees, dead audit sources).
- ticker.map GLOBAL/DELETE rules now apply to crypto accounts (a new
  mapped stage; the README's "every stage" contract held everywhere
  but there). taxjson-ticker-map grows --map/--global-only.
- The blended pass's diagnostics (the only pass that sees
  --sheltered) are mirrored into each account's .sum banner instead
  of dying unread in a dot-file.
- taxjson audit no longer crashes (NameError) on projects with two or
  more taxable equity accounts; uppercase `country = "CA"` no longer
  crashes the blended pass mid-run (normalized at all stage sites).
- A failed pipeline stage exits with a one-line message pointing at
  the child's error instead of a stacked CalledProcessError traceback.

Contracts/docs:
- The README's return-of-capital `.tt` example was in the WRONG FIELD
  ORDER and crashed the parser (with the roc-sum footer pointing
  straight at it); corrected, ADJUST's five-field shape documented,
  and malformed .tt rows now produce a clean one-line error.
- RBC exports are no longer mis-detected as Webull ("Account Number"
  boilerplate matched Webull's marker; the repo's own demo produced
  silently empty books) — pinned end-to-end on both demo files.
- .tt `time` documented as REQUIRED (omitting it misparses); the
  radar's category list gains the missing EXITABLE and CAUTION;
  sector.map removed from the file table; the [estimate] block added
  to the schema docs and the init scaffold; chained `--json` output
  caveat documented; GUI Tax tab documented.

## v0.11.0 — 2026-09-03

The deep audit and the engine-correctness campaign: 31 confirmed audit
findings fixed, three further engine bugs found by conservation
fuzzing, and a standing rigour suite (invariants, differential,
mutation harness). 1,883 tests.

Engine rigour: property-based invariant testing, and the two bugs it
found on its first runs.

- Fuzz generator widened: corporate SPLITs mid-history, same-timestamp
  collisions, sheltered round trips (buys AND sells), non-flat ending
  positions; the conservation identity generalized to
  realized − parked deferrals − permanent == no-wash baseline, so
  open-ended books are covered too. 2,000 books per engine clean.
- NEW: blend-vs-split differential law — the split of the blended
  taxable pass may create or destroy nothing: disposition counts,
  gain totals, and per-symbol inventory must all conserve across
  `taxjson-split-gains` artifacts vs the blended document. Fuzzed
  alongside the engine invariants.
- NEW: targeted mutation testing over the wash-relevant regions of
  core.py and pipeline.py (`scripts/mutation_audit.py` +
  `mutation_triage.py`, committed for reruns): 298 mutants across
  four rounds. The gaps it exposed are now pinned by goldens — the
  engine's own ±30-day boundary on BOTH edges (rebuy day 30 denies /
  day 31 allows, exit day 30 rescues / day 31 too late, including
  the window-widening mutant that hides behind the still-held
  bound), the still-held direction gate (long loss + net-short
  balance), cross-symbol trigger leakage, same-day post-loss
  allocation priority, and crossing-sale self-triggering. Residue
  census: 118 candidates dominated by option-premium paths,
  split-rename walks and inventory-merge orderings (tracked as the
  next kill-set targets), 72 epsilon-boundary equivalents, plus
  known-equivalent creation-sign mutants. Engine branch coverage
  under the kill set: 77%.

- **NEW: conservation fuzzer** (`tests/test_engine_invariants.py`) —
  seeded-random fully-liquidated books checked against LAWS of the
  domain rather than authored examples: realized + permanent denials
  must equal the no-wash baseline exactly (every deferral recovered);
  disallowances only ever on losses; input order must not matter;
  signs never negative; the solver converges. 25 books per engine in
  the suite; `TAXJSON_FUZZ_BOOKS=2000` for the extended run (clean).
  Plus CRA golden cases (full denial, the least-of-three partial
  formula, no-acquisition-no-denial, full-group-exit rescue) encoded
  from the published guidance as an oracle.
- Fuzzer find #1: **a deferral is only real to the extent TAXABLE
  still-held shares back it at the window's end.** Allocating purely
  by trigger let a taxable trigger whose own shares were gone by +30
  collect a deferral that parked on an empty pool and never recovered
  — denied-but-sheltered-backed portions are PERMANENT (s.53(1)(f)
  bumps the basis of property still owned; a bump inside a registered
  account is moot). This is the FFH.TO shape, now accounted
  correctly: permanent, not deferred-and-stranded.
- Fuzzer find #2: **a deferral landing on a FLAT pool now parks
  direction-agnostically and takes its sign from the NEXT opening.**
  The creation-sign fallback leaked a short-signed deferral into a
  subsequent long opening with inverted effect — the denial
  double-counted instead of recovering.
- The AmbiguousTransferDateError message now carries the exact
  counter-TRANSFER .tt recipe for the custody-move resolution.

Deep audit (2026-09): four parallel audits over the engines, transfer
handling, broker acquisition, and the decision commands produced 31
confirmed findings, all fixed and pinned by regression tests. The ones
that changed NUMBERS or ADVICE:

- **Engine (Canada):** a short-side superficial-loss deferral landing
  on a net-LONG symbol-global pool applied with an inverted sign —
  REDUCING long ACB instead of deferring — and a disallowance from an
  earlier solver iteration was never retracted when basis adjustments
  turned that disposition into a raw gain (a denied "loss" reported on
  a gain). Deferral ADJUSTs now key their sign off the pool's actual
  direction at the landing site, and stale DISALLOWs retract on
  re-iteration. Conservation (realized + deferred = no-wash baseline)
  is pinned by test.
- **Sheltered in-kind contributions now trigger superficial-loss
  detection.** Parsers emit TRANSFER for an in-kind contribution into
  an RRSP/TFSA; the wash context stripped ALL transfers, so the
  canonical permanently-denied superficial loss was silently claimed.
  Custody noise still nets out (same-account pair drop + a new
  registered-to-registered cross-account netting); what survives is
  rewritten to a sheltered acquisition the wash walk can see.
- **Transfer pair-cancellation is time-clustered** (35-day segments):
  an in-kind contribution OUT of a taxable account (a CRA deemed
  disposition) months before an unrelated transfer IN no longer
  cancels silently past the taxable hard-error; a SPLIT between the
  legs now blocks the drop (share terms changed).
- **Wash radar:** custody-move transfers are netted out of its input
  and never count as acquisitions (a pure broker move no longer
  fabricates a VIOLATION demanding liquidation); a sale crossing
  long->short prorates proceeds to the closed portion (a real loss no
  longer computed as a gain -> CLEAR); the flip-side opening is
  recorded; zero-book-value transfer-ins carry average cost instead
  of diluting ACB to zero.
- **buy-check / sell-check:** a VIOLATION leg poisons the whole
  class's dates (no more too-early "safe to buy from" off a sibling
  BLOCKED leg); sell-check exports the binding rescue deadline as
  `act_by` (EARLIEST across the class) separate from the
  safe-to-sell `clears_at` (latest); the sheltered-triggered
  VIOLATION message now states the actual still-held condition
  instead of contradicting its own rescue advisory; OCC option
  symbols fold to their underlying's class (an option is a right to
  acquire identical property).
- **harvest:** VIOLATION/EXITABLE/CAUTION losses bucket as claimable
  NOW (full exit) — their dates are deadlines, not availability;
  the old schedule planned harvests for after the point of no return.
- **watch:** same-category advisory changes (a violation's required
  sell quantity doubling) are reported; the "(a new buy extended the
  window)" explanation only prints when the date actually moved later
  on a window category.
- **estimate:** `[estimate]` config values pass the same
  non-negative/finite guard as the CLI flags (a negative
  `other_losses` fabricated taxable gains, feeding instalments too).
- **instalments:** "Behind by" counts only instalments DUE so far; an
  on-schedule mid-year taxpayer sees "On schedule so far" with the
  year's remaining total.
- **IBKR parser:** Flex files carrying multiple detail levels
  (Order + Trade + ClosedLot per fill) emit each trade once;
  cancelled-trade pairs (code `Ca`) net to exactly zero instead of
  booking a phantom 2x-commission round trip; a cancel/rebook
  RESTATED corporate action consumes its original spinoff rows
  instead of double-counting income and shares.
- **Questrade parser:** TSX-Venture `.VN` normalizes to `.V`
  (was the junk `ABC.VN.TO`, fragmenting identity vs live holdings);
  a transfer-in keeping an internal symbol code (`R223608`) warns.
- **fetch:** `--trim-overlap` is bounded at BOTH window ends — it
  could delete sibling rows dated after the window's end, i.e. real
  trades the fetched file does not own; likely broker restatements
  (an existing row matching a fetched row on date/symbol/type but
  differing elsewhere) are named at fetch time; the rotated Questrade
  token is written 0600 from the first byte; live-holdings option
  suffixes also learn Canadian roots from the account's own books
  (cash-secured puts no longer phantom-mismatch in verify).
- **taxjson audit:** the tie-out now fails on OMISSIONS and
  FABRICATIONS (a saved gains file missing a whole disposition — or
  carrying an invented one — passed with exit 0), and ties the
  in-scope totals; payment-in-lieu records are excluded on both
  sides; blended split apportioning dedupes per-broker duplicate
  SPLIT rows (Σ per-account = blended again).
- Also: the engine no longer crashes on a loss row with `time=''`.
- **Radar EXITABLE with a standing sheltered holding no longer says
  "full exit is fine".** CRA's denial is min(sold, acquired-in-window,
  held-at-+30) and old sheltered shares keep the still-held term
  alive — a full TAXABLE exit still leaves up to the sheltered
  balance denied permanently. The advisory now says so with the
  quantity (encoded heuristic disproved by a real FFH.TO trade).
- **Ambiguous transfer dates refuse to guess.** A sheltered
  TRANSFER-in whose ARRIVAL date lands inside a superficial-loss /
  wash-sale trigger window now raises a hard error naming both
  resolutions (import the prior broker's history so the pair nets, or
  record the true acquisition as a BUYSELL) — a custody-move arrival
  is not an acquisition, and silently treating it as one could
  wrongly deny a loss just as the old silent strip wrongly allowed
  one. Outside trigger windows the rewritten row counts only toward
  the date-insensitive still-held balance, which is always factual.

Real-book impact: one disposition's denial changes (a superficial
loss whose still-held test now sees transfer-contributed sheltered
shares) — re-run `taxjson run` to refresh saved books after
upgrading; `taxjson audit` names any disposition whose figure moved.

## v0.10.0 — 2026-08-29

The pre-production compat purge. 1,840 tests.

- Pre-production compat purge: every development-era spelling that was
  kept as a hidden alias or no-op is REMOVED — the code has no
  deployed users to keep working, so the old names now fail loudly
  instead of being silently accepted. Gone: `run --force` (full
  rebuild IS the default; `--fast` opts into the cache),
  `sum --estimate` (`taxjson estimate` is the one front door; sum
  still renders the estimate block when income inputs are supplied —
  the GUI's what-if path), the hidden `--account-name` aliases on
  `taxjson-brokerage` and `taxjson-wash-radar` (renamed to
  `--account` in 2026-07), the `taxjson-fees` console-script alias of
  `taxjson-fees-sum`, the hidden `fees-sum --year` alias of the
  PERIOD positional, and the fold-in migration for the pre-naming
  `inputs/<acct>/questrade_api.csv` fetch file. `taxjson-fees-sum
  --since` sheds its deprecation costume — it is the PERIOD wrapper's
  cutoff channel, now a documented flag. The now-unused
  `deprecated_alias` argparse helper is deleted. Kept deliberately:
  the `.tt` plain-split lint tolerance and parser-format tolerances —
  those cover DATA files users author, not UI churn.

## v0.9.0 — 2026-08-29

The authoritative audit command, a machine-wide Questrade token home,
and the removal of the portfolio-analytics commands. 1,841 tests.

- REMOVED: the portfolio-analytics commands `value`, `timeline`,
  `yield` and `sold-perf`, and the never-exposed standalone tools
  `taxjson-beta`, `taxjson-sharpe`, `taxjson-atr`, `taxjson-hv` and
  `taxjson-leaps-missed` (~1,900 lines plus their tests). None fed
  any tax number — they were portfolio-tracker features living in a
  tax toolkit, priced over the network (the brittle dependency
  class), and portoml-ai is their proper home. `divs-sum` keeps the
  tax-relevant half of `yield` (dividends actually received);
  `harvest` keeps the pricing chain (and the `[ibkr]` extra) for the
  one decision that needs live prices. The `[analytics]` extra, the
  `sectors_file` setting, and the desktop app's Value tab (a chart
  over `taxjson value`) are gone with them.
- Questrade token: `~/.questrade_token` is now THE token home
  (`$QUESTRADE_TOKEN_FILE` overrides) instead of the per-project
  `work/.questrade_refresh_token`. Questrade runs one rotating chain
  per API app, so the credential belongs to the machine, not to a
  project — two projects (or taxjson next to portoml-ai) each caching
  their own copy meant whichever ran last held the live token and the
  other failed to authenticate. The resolver is exactly two steps
  ($QUESTRADE_TOKEN_FILE > ~/.questrade_token, written mode 600); a
  leftover legacy work file is ignored entirely — never read or
  written — and can be deleted. First run on a machine without the
  shared file: pass `--refresh-token` once (or set
  $QUESTRADE_REFRESH_TOKEN).
- **`taxjson audit [SYMBOL ...]`** — the authoritative justification
  of every capital-gain figure. One block per taxable disposition:
  the parsed broker row it came from (nominal currency, original
  ticker, source file — joined by the content-hash transaction id),
  the ticker.map rule that renamed it, the exact FX rate the pipeline
  applied (same file, same date-resolution rules, provenance named:
  exact date / carried forward / default) with nominal x rate
  recomputed against the base books to the cent, the engine's
  disposition math from a trace-enabled re-run of the same blended
  computation the pipeline runs (ITA s.47 cross-account ACB / US
  cross-account §1091), the superficial-loss / wash-sale
  determination with each replacement lot resolved to its row and
  the denied loss followed to where it went, a to-the-cent tie-out
  against the pipeline's saved gains files, and the full ACB/FIFO
  pool trace. A RECONCILIATION footer counts every cross-check;
  exit 1 when any disagrees — the audit's claim is precisely that
  these numbers agree, so a mismatch is a finding. `--summary`,
  `--id`/`--date`/`--account`/`--year`/`--all-years` filters,
  `--no-trace`, `--json`; the standalone `taxjson-audit` takes
  explicit paths for use outside a project.

## v0.8.0 — 2026-08-27

Deep audit of the instalments, estimate/AMT and
wash-check features, with the advice-changing fixes
called out below. 1,903 tests.

- Audit pass over the new commands, with the fixes that changed
  ADVICE rather than formatting:
  - `buy-check` no longer prints a VIOLATION's `clears_at` as a
    "safe to buy from" date. That date is the SELL-BY deadline for
    rescuing the loss — quoting it as a re-entry date invited the
    rebuy roughly a month early, permanently killing the loss the
    command exists to protect. Violations now say to wait 31 days
    past the latest in-window loss sale.
  - Across a class of cross-listings both checks take the WORST
    (latest) clearing date, not whichever ticker sorted first.
  - `sell-check` stops telling a sheltered-ONLY holding to "sell at
    a loss" — a registered disposition has no tax effect at all.
  - A VIOLATION on a name a registered account also holds is UNSAFE,
    not the rescueable ACTION: the registered-matched portion is
    permanently denied and selling cannot recover it. The radar's
    taxable/sheltered quantity split is carried through to the
    checks so they can tell these cases apart.
  - The last-loss sanity line dates the sale by SETTLEMENT, the same
    basis the radar's ±30-day windows use; a trade-date age
    contradicted the verdict for anything sold 31-32 days ago.
  - `verify` surfaces a configuration failure as its own message and
    count instead of reporting it as a broker mismatch, and says why
    it needs Questrade (Flex statements carry no live-position feed).
  - `--tolerance 0` means exact; falsy coercion had restored the
    1e-4 default.
  - Live Questrade positions: a trailing CLASS letter is not an
    exchange — `BRK.B` is `BRK.B.US`, and passing it through
    unsuffixed made every class-share position look like a phantom
    mismatch in the verify diff.
  - Rows with a blank category are no longer captioned `(CLEAR)`.
- `estimate` and `instalments` read the same `[estimate]` config, so
  the current-year instalment basis cannot silently differ from the
  estimate it claims to follow.
- AMT: `other_losses` is capped at the realized gain before the 50%
  disallowance, so a loss pool larger than the year's gains stopped
  driving adjusted taxable income negative.
- The derived-dividend-rate snap now requires the shortened rate to
  be UNAMBIGUOUS — if a neighbouring value at the same precision
  also explains the cash, the raw quotient stands. Where the cash
  genuinely cannot resolve the rate (7 shares paying $0.26 fits both
  0.037 and a declared 0.0375) the shorter form is kept and claims
  no precision the data cannot back.
- `fx-cash` labels its report ESTIMATE ONLY — it is reconstructed
  from broker cash flows, which do not carry conversions or
  deposits.
- README: documents `--json` on both checks, `verify --tolerance`
  and its Questrade-only constraint, `fetch`'s window and credential
  flags, and `estimate --province` / the `[estimate]` block. The
  Status section now says plainly what the test suite does and does
  not assure.

- **`taxjson instalments`** — Canadian tax instalments from an
  `[instalments]` config: the four due dates (weekend-rolled) under
  the current-year, prior-year, or CRA-reminder basis; what each
  calls for vs what was paid; the ITA 161(2) **offset interest**
  (daily-compounded, charge netted against credit); and the ITA
  163.1 **penalty** (half the excess over the greater of $1,000 and
  25% of the no-payment interest). The current-year basis is driven
  by `taxjson estimate` itself — net tax owing = total tax + any AMT
  top-up − amounts withheld — so the two commands cannot disagree.
  `estimate` gains a compact instalment block when configured, and
  `--json` carries the schedule and interest on both. The prescribed
  rate accepts a dated schedule (`prescribed_rates`) as well as a
  scalar — CRA resets it quarterly and charges each day at the rate
  then in force, so the daily walk looks the rate up per day and the
  report names every rate it used. Interest is assessed on the LEAST
  of the methods the configured figures support (ITA 161(4.01)), as
  CRA does — following any one basis correctly is interest-free — and
  the report names the governing basis. Payments accept an optional
  `note` and are listed. CRA's full two-limb requirement test is
  applied (net tax owing over $3,000 in the current year AND in
  either of the two preceding years), so configured prior-year
  figures at or below the threshold report "no instalments required"
  — naming the failing limb and warning that a placeholder zero
  reads as "I owed nothing" and suppresses both the obligation and
  all interest. Per-date status reads PAID / LATE / MISSED /
  UPCOMING — "SHORT" conflated a date covered late (interest ran, but
  nothing is outstanding) with one still owing.
- `taxjson init` scaffolds the current feature set: `province` (which
  `estimate` requires for Canada), `sectors_file`, `fx_cash_gains`,
  the per-account fetch keys, and a commented `[instalments]` block
  on Canadian projects. All comments — a fresh project parses to
  exactly the same live config as before.

- Estimate: the AMT block is rendered in the house style — the
  report's 30/14 column widths, prose wrapped at 78 columns, no
  over-wide header, and the assumptions footer separated and wrapped.
  The rule summary now rides in a note printed in both the binding
  and non-binding cases.
- Dividends: a DERIVED per-share rate (broker states only the cash
  and the share count) snaps to the fewest decimals that still
  explain the paid amount to the cent. Back-computing manufactured
  spurious precision — 37 shares paid $20.54 showed 0.55513514 for a
  dividend declared at 0.555, disagreeing with the same payment in
  another account whose statement states the rate. Genuinely
  fine-grained rates (0.3728) survive; stated rates are untouched;
  cash amounts never move.

## v0.7.0 — 2026-08-26

The Canada AMT check. 1,827 tests.

- Canada AMT check in `taxjson estimate` (post-2024 rules: capital
  gains at 100% inclusion, dividends un-grossed with no DTC, credits
  at 50%, 20.5% over the bracket-pinned exemption, ON/BC/AB
  piggyback). Rendered always — the top-up and 7-year carryforward
  when it binds, the headroom when it doesn't; `estimate.amt` +
  `estimated_tax_with_amt` in JSON. US projects: investment income
  alone rarely triggers US AMT (LTCG keep preferential rates inside
  it) — documented as out of scope, not faked.

## v0.6.0 — 2026-08-26

Three new commands rounding out the decision loop: what will this
year cost me (`estimate`), and is this specific trade wash-safe in
either direction (`buy-check` / `sell-check`). 1,822 tests.

- **`taxjson sell-check SYMBOL ...`** — the sell-side twin: UNSAFE
  when a recent affiliated buy would deny the loss (LOCKED), ACTION
  when a rescueable violation is open, SAFE*/SAFE with caveats. Both
  checks share one symbol-class engine (known-exchange roots +
  ticker.map equivalences) and the last-loss sanity line; both note
  when sheltered context is missing.
- **`taxjson buy-check SYMBOL ...`** — buy-side wash check on the
  combined radar: UNSAFE (with the safe-from date) when a loss was
  sold in the past 30 days, SAFE* when buying merely extends an open
  wash window; root-matched across listings; exit 1 on unsafe.

- **`taxjson estimate`** — the tax estimate as a first-class command:
  the realized-gains summary table followed by the estimate block
  (same code path as `sum --estimate`, which keeps working). The FTC's
  actual-withholding read now comes from the BASE books' TAX rows,
  year-scoped — the gains files never carried them, so the 15%
  assumption always silently won before.

## v0.5.0 — 2026-08-25

Live broker verification, plus advisory and corp-action correctness
fixes proven on real data. 1,807 tests.

- **`taxjson verify`** — fetch LIVE Questrade holdings (positions
  endpoint) and cross-check them against the computed books through
  the sanity machinery; exit 1 on mismatch. `fetch --positions`
  writes the same `work/<account>_live_holdings.toml` snapshots.
  Option symbols convert to OCC (Montréal-listed roots get .TO);
  `.VN` maps to `.V`; bare symbols are US listings. The complete
  integrity loop is `taxjson fetch run verify` — proven live: it
  flagged 4 real stale-book discrepancies, pulled the missing rows,
  and reconciled both accounts to the broker exactly.
- **RISK reinterpreted**: s.40(2)(g) needs an acquisition INSIDE the
  ±30-day window, so a sheltered-held position with no buys in the
  past 30 days is sellable at a loss NOW — RISK losses count in
  harvest's claimable-now bucket with a forward-window caveat (pause
  DRIPs/sheltered adds for 30 days after selling; an affiliated buy
  makes the denial permanent). Previously bucketed as unharvestable.
- **Questrade spinoff chains**: the placeholder/reversal/delivery
  triple (real DFDVW warrant case) is grouped by its REC/PAY +
  ON-N-SHS chain identity — no more empty-symbol events (which
  emitted invalid book rows) or skipped net-zero chains; targets are
  currency-suffixed like trade rows; unresolvable chains skip
  loudly; a failed sheltered-book validation exits cleanly instead
  of a traceback.
- Harvest: currency labels stacked under the money column headers
  (no column widening); money/qty columns right-aligned.
- Every subcommand's `-h` shows a synopsis, and help pages wrap at
  78 columns (the top-level command blob is a COMMAND metavar).

## v0.4.0 — 2026-08-21

The fx-cash feature, the fetch/watch automation polish, and the
2026-08-21 high-effort audit (four parallel review passes over the
whole tree; every confirmed finding fixed and pinned). 1,798 tests.

- 2026-08-21 audit fixes — engine: SPLIT-rename schedule migration is
  input-order-independent (§1091 unit conversion); blended split nets
  fee rebates and warns when phantom shares can't be attributed;
  transfer-drop guard is account-scoped and counts ASSIGN; NaN prices
  can no longer fossilize in the price cache or reach --json output.
- 2026-08-21 audit fixes — flow: a post-conversion currency INVARIANT
  fails validation on residual native rows; the Canada estimate's FTC
  comes from actual TAX rows (capped at the treaty ceiling); holdings
  export stops summing cross-currency costs (mixed_currency +
  per-currency components); leaps-missed totals are per-currency;
  t1135 honors zero-ratio renames; deterministic ADJUST ordering.
- CLI: chains validate fully before executing anything (no more
  half-run on a bad tail segment) and ambiguous boundaries print a
  note; errors name the executing command; `elect --json` emits the
  saved elections; `sum` takes an [account]; wash-radar rejects
  sheltered accounts and notes missing sheltered context; empty-state
  JSON documents keep their full key schema; ibkr_flex refuses
  multi-account downloads; GUI panes name their currency.
- Fetch hardening: --trim-overlap backups never clobber; the trim
  date test parses real dates; prior years' own fetch files no longer
  false-alarm the overlap warning; Flex network errors are clean;
  --dry-run touches nothing and reports would-be overlaps; fetched
  CSV writes are atomic.
- Fetch: `--year N` backfills a past tax year into `questrade_N.csv`
  (window capped at Jan 15 of N+1); `--json` machine summary with
  per-activity-type counts; the text output gains the same type
  summary. Watch: `--state PATH` lets multiple cron cadences keep
  independent baselines.

- **`taxjson fx-cash`** — FX capital gains on foreign-currency cash
  (ITA s.39(1.1) with the $200 de minimis; §988 ordinary-income
  figure for US projects), reconstructed as a per-currency ACB cash
  ledger from the taxable accounts' native books. Standalone report;
  opt-in end-of-run summary via `fx_cash_gains = true` — no other
  number changes either way.

- Fetch config lives on the account: `brokerage` +
  `account`/`query_id` under `[accounts.<name>]` (replaces the
  short-lived `[fetch.<name>]` tables from v0.3.0).
- Questrade fetch always covers the full tax-year window (from Dec 15
  of the prior year, so year-boundary trades that settle in January
  are never missed) and surfaces the API's real error messages.
- Questrade fetch writes tax-year-stamped `questrade_<year>.csv` (a
  legacy `questrade_api.csv` is folded in and removed after a
  successful merge).
- Fetched Questrade rows now reconcile with manual exports
  row-for-row: Transaction Date maps from `tradeDate` (the API's
  `transactionDate` is the posting date), description whitespace is
  collapsed, and chunk-boundary duplicate activities are dropped.
  Manual CSVs overlapping the fetched window are flagged (the two
  sources round price/gross differently, so duplicates never dedup);
  `--trim-overlap` trims them with a `.bak` backup.

## v0.3.0 — 2026-08-20

Two new automation commands plus the 2026-08 flow-consistency audit
(four parallel review passes; every fix verified by an adversarial
agent pass and pinned by a regression test). 1,746 tests.

- **`taxjson watch`** — cron-able change detector: reports only what
  changed since the last run (new/changed/cleared radar advisories,
  moved clear dates; `--harvest` adds the harvestable-now total).
  Silent with exit 0 when nothing changed.
- **`taxjson fetch`** — broker auto-fetch: Questrade REST API and
  IBKR Flex Web Service, per-account `[fetch.<account>]` config,
  writing the same file formats the parsers already read.
- 2026-08 flow audit fixes: `value` no longer double-converts
  ACB-sourced price.map series; the filed-year check recomputes US
  crypto with `--no-wash` (no more false drift); `yield` DIV/SH nets
  same-day reversals and no longer mixes currencies; a combined
  cross-account wash-radar sidecar (`wash_radar_COMBINED`) feeds
  harvest's ADVISORY; fee rebates net into `.sum` FEES; `close-year`
  hard-stops on stale wash artifacts; basis labels everywhere follow
  the actual resolved files; `--fast` notices deleted map files;
  RBC TAX rows keep the traded market's suffix.
- `taxjson list --negative` — show only negative-quantity positions
  (real shorts, or missed corporate actions / import gaps in accounts
  that can't short).
- Questrade: STK DIV rows (stock dividends paid in shares) now enter
  the book as in-kind deliveries instead of being silently dropped;
  a stderr NOTE points taxable accounts at `distributions.map` for the
  declared amount.
- Coinbase: "Incentives Rewards Payout" rows recognized as reward
  income (FMV income event + acquisition), like the staking family.
- IB: split ratios are snapped to the broker's own Corporate Actions
  leg quantities (IB books fractional results to 4 dp), eliminating
  post-split phantom dust.
- Documentation overhaul: repaired README subcommands table, new
  "Project layout and configuration" (full taxjson.toml schema, every
  project file) and generic-importer sections, EXIT@ explained,
  KNOWN_ISSUES brought current.
- Internal simplification: dead code removed; quantity formatters,
  UTC-noon date helpers, soft config reads, comment-strip JSON
  loaders, and tomllib fallbacks each consolidated to one home.

## v0.2.0 — 2026-08-17

First tagged release. Everything below landed since the 0.1.0 scaffold;
the cycle included three full audit → fix → adversarial-verification
rounds (August 2026) on top of the 2026-06/07 audit series.

### Engines & correctness

- **Blended multi-account taxable pass** — the canonical wash-adjusted
  numbers are computed by one combined run over all taxable equity
  accounts: Canada ACB blends across non-registered accounts (ITA
  s.47); US §1091 wash sales match across accounts while FIFO basis
  stays per account (`taxjson-gains --per-account-basis`,
  `taxjson-split-gains`). Per-account `<name>.sum` remains the isolated
  pre-blend baseline for comparison.
- Dozens of engine fixes from the 2026-07 FUZZ/REVIEW series and the
  2026-08 deep audits, including: wash-sale unit conversion across
  splits and renames, §1223(3) holding-period tacking, retained-share
  replacements, sheltered-account partitioning, assignment-premium
  attribution (per account, marked-leg scoped, time-scoped),
  cash-settled index-option assignments, ROC ADJUST handling, and
  share-conservation post-conditions in both engines.
- US crypto exempted from §1091 (property, not securities); Canadian
  crypto stays superficial-loss-checked, including in the wash radar.

### Filing outputs

- IRS **Form 8949** (code-W wash adjustments, Schedule D totals, real
  per-lot acquisition dates, correct short-sale columns) and CRA
  **Schedule 3** (line 13199/13200, superficial-loss notes).
- **TXF export** (`form-export --form txf [--box A|B|C] --out f.txf`)
  for TurboTax import, built on the 8949 model.
- **T1135** screening and per-property/per-country tables; broker slip
  reconciliation (`reconcile-slips`, trade/settle date-basis aware);
  capital-loss **carryover ledger** (Canada + US worksheets).
- **Filed-year lock**: `close-year` snapshots a filed year;
  `check-filed` (and every full run) recomputes it from the current
  books — blended-aware — and reports drift; fatal under `--strict`.
- **`distributions.map`**: reinvested (phantom) capital-gains
  distributions and late-published ROC factors become ACB adjustments
  automatically.

### Importers

- Interactive Brokers, Questrade, RBC Direct Investing, Webull,
  Kraken, Coinbase — with sign-preserving income conventions, loud
  refusal of unrecognized layouts, split-fill dedup protection, and
  zero-drop skip accounting throughout.
- **Generic column-mapped importer**: any other broker via
  `generic_*.csv` plus a TOML mapping (template:
  `examples/generic_wealthsimple.toml`); strict numerics, ambiguity
  refusals, mapping files participate in cache invalidation.
- Crypto: Coinbase Convert and Kraken crypto-to-crypto two-leg
  emission, legacy pair formats, fee-inclusive instant-trade totals,
  FMV backfill with correct currency stamping.

### Workflow & CLI

- **Chained subcommands**: `taxjson run sum`, `taxjson run --fast sum
  --json` — trial-parsed boundaries, per-command flags, fail-fast exit
  codes.
- `taxjson run` — single-command pipeline with in-process stage
  dispatch, `--fast` incremental cache (deletion-aware), `--strict`
  validation mode, headless corp-action elections (exit 3 +
  `taxjson elect`), and a filed-year drift check at the end of every
  full run.
- Advisory tools: wash radar (correct rescue deadlines), harvest
  (recovery schedule, `EXIT@` FX-aware break-even exit price with 2%
  buffer), div-yield, value, sold-perf, fees, t1135, sanity,
  carryover, `--version`.

### Apps & platform

- Local web UI (FastAPI, localhost-only, what-if sell engine) and a
  PySide6 desktop GUI (background pipeline runs, elections dialog).
- CI: ruff critical tier, 3.9–3.13 × ubuntu/macos matrix, GUI
  offscreen cell, all-extras cell. 1,660+ tests.

### Known limitations

See `KNOWN_ISSUES.md` — notably: RBC withholding gross-up assumes the
15% US treaty rate, Questrade emits no standalone INTEREST/TAX rows,
Webull option expiry/assignment needs a sample CSV, §1256 (60/40
mark-to-market) is not implemented, and the tax estimate does not
classify eligible/qualified dividends.
