# Changelog

## Unreleased

- **holdings.toml states what its base-currency cost is**:
  `meta.base_cost_basis` says `base_total_cost` is per-account and
  per-listing, before superficial-loss adjustments and the s.47 blend
  (the filing ACB is `taxjson list`) (audit S037-24).
- **Corporate actions follow ticker_extraction_overrides.txt.** The
  overrides renamed a security's trades but not its merger/spin-off rows,
  so the event consumed an empty pool under the broker's spelling while
  the real position stayed put (a taxable merger's gain vanished). The
  corp-action rows now take the same rename; when the overrides rename
  some of a spelling's rows and not others, a corporate action on it is
  refused instead of guessed (audit S004-00).
- **Election hints must be non-negative amounts.** `elect --hint`, the
  interactive prompt and a hand-edited manifest.json refuse a negative,
  nan or inf hint (a negative allocated ACB created basis from nothing,
  a negative FMV booked negative dividend income, and nan was saved to
  fail only on the next run) (audit S039-00).
- **audit fails when a configured account has no books** (it printed
  "✓" over the others and exited 0, and the checklist marked every
  disposition tied) (audit S047-16).
- **The checklist's run-clean step flags unblended books.** With two or
  more taxable equity accounts and no blended pass (`run --account` on
  each, or a run stopped at pending elections) the filing figures are
  per-account ACB; run-clean now says so instead of "done", and every
  command that reports the run state carries it (audit S004-07).
- **LEAPS views: long positions only, through ticker.map, with the
  registered accounts split out.** `leaps` / `leaps-sum` no longer count
  the write and buy-back of a contract that qualified through a long buy
  (those legs are covered-call P&L, already in `ccd-sum`), a LEAPS whose
  root ticker.map TOBASE-renames (BCE...US -> BCE...TO) is no longer
  dropped, and `leaps`, `leaps-sum` and `ccd-sum` print the TAXABLE and
  SHELTERED parts of their total (`--json`: `taxable_gain`,
  `sheltered_gain`) (audit R1-172, R1-237, R1-182).
- **`list --date` applies phantoms.json**, so phantom-backed positions no
  longer show as large shorts. Plain `list` is labelled with the date the
  books run to, not "as of tax year" (it shows end-of-data positions),
  and `--date` no longer claims "pre-ticker.map" (audit R1-187, R1-282).
- **`fetch` never loses input activity.** `--trim-overlap` finds the
  trade date by the "Transaction Date" header (a manual export with
  Settlement Date first lost a trade the API file did not hold) and
  writes the trimmed file atomically at 0600; the overlap guard sees an
  upper-case `.CSV` sibling like `run` does; an IBKR Flex download that
  does not cover the tax-year activity already in `ib_flex.csv` is
  refused (saved as `ib_flex.csv.new`), the replaced file is kept as a
  numbered `.bak`, and a past year's download that does not span the
  year warns (audit R1-74, S046-14, S007-00).
- **buy-check / sell-check read broker spellings and Montreal option
  roots.** RCI-B, "RCI B", RCI/B and RCI-B.TO are read as RCI.B(.TO) (they
  answered SAFE beside a loss on RCI.B.TO), and an option on a root that
  names no share listing but exactly one class share of it (RBC's
  RCI270115C00046000.TO for RCI.B.TO shares) is in that share's class
  (audit S007-02, S047-01).
- **The wash tools name a taxable account with no books.** wash-radar,
  watch, buy-check and sell-check warned about nothing when a configured
  taxable account's base book was missing, so a sibling's recent buy read
  as SAFE (audit S046-11).
- **The web radar opens on the COMBINED view** when there are two or
  more taxable accounts, and a per-account view says it sees only its own
  book (it read "CLEAR — safe to sell at a loss" while a sibling's buy
  made the loss superficial) (audit R1-229).
- **Radar sidecar names follow the account name exactly**: account
  `a_base_x` no longer overwrites account `a_x`'s radar, and an account
  named COMBINED is refused (audit S038-10).
- **`run --fast` sees content, not only mtimes.** Each account's input
  files and the project-root maps (ticker.map, overrides,
  distributions.map, phantoms.json, crypto_ticker.map) are fingerprinted
  by content; a CSV replaced by an export with an older mtime, or a
  ticker.map restored the same way, kept the old parse at exit 0 (audit
  R1-253, R1-294).
- **FX rates: freshness is judged per currency.** to_base.csv holds one
  block per source currency; a USD block cut short by a failed download
  hid behind a fresh AUD last line and was served for days. Each
  configured currency must now reach the last few days (audit S046-06).
- **A full run removes sidecars the config no longer produces.**
  work/sheltered_base.json after the last sheltered account is removed
  (the filed-year lock and the radar kept reading it), and the
  `_gains_wash.json` / `_wash.sum` of an account re-typed to sheltered
  (every query preferred them) (audit S004-05, S038-19).
- **`taxjson-merge` fails on an unreadable input** instead of writing a
  book without it (`run --account <sheltered>` rebuilt
  sheltered_base.json without a sibling's rows) (audit S038-18).
- **The FILING REQUIRED reminder survives an unreadable manifest.json**:
  the file is named and the other accounts' reminders still print
  (audit S038-23).
- **A spreadsheet in an account subfolder** gets the same "not read"
  warning as a CSV there (audit S043-13).
- **Broker detection: prefixes, then content, then venue words.** A
  `generic_`/`cb_`/`kr_` prefix now always wins, and an IB, Questrade or
  Webull export is recognised by its content even when its name mentions
  coinbase or kraken; `generic_kraken_export.csv` and
  `kr_trades_moved_from_coinbase.csv` went to the wrong crypto parser
  (0 rows, exit 0), and an IB export named after Kraken Robotics was
  refused as crypto data. The crypto/equity refusal names the files
  (audit R1-127, S044-01, S044-02).
- **taxjson.toml is checked the same way by every command.**
  `base_currency` is trimmed and upper-cased (" CAD" failed later with a
  misleading "fix the rates file" error; "cad" priced CAD fees at the
  default FX rate) and must be a 3-letter code; a Canada project with a
  non-CAD base warns. `[settings] year` must be a plausible tax year
  (1900 to next year, like `init`), and `run` warns when no transaction
  in the books falls in the configured year. A misspelled top-level
  table (`[estimates]`, `[instalment]`) and an unknown `[estimate]` /
  `[instalments]` key now warn in `estimate`, `sum` and `instalments`,
  not only (or never) in `run`. `[instalments]` withheld, prior-year
  figures and prescribed rates must be non-negative finite numbers
  (rates below 1); a TOML boolean is refused there and in `[estimate]`
  (audit R1-153, R1-256, R1-216, R1-257, S038-13, R1-217).
- **Net tax owing guidance names the right lines.** The init template,
  README and the instalments report said "line 48500 minus withholding";
  48500 also subtracts the instalments paid, so following it read "no
  instalments required". They now give CRA's instalment-chart
  definition (42000 + 42200 + 42800 (+ 43200) minus 43700 and the
  refundable credits) (audit R1-215).
- **`init --force` never overwrites an earlier backup.** A second
  `--force` replaced taxjson.toml.bak (the user's config) with the first
  template; later backups are numbered (audit R1-255).
- **harvest: claimable-now losses and option marks that match the
  books.** A LOCKED position counts the part of its loss a sale today
  keeps as claimable now (only the units a registered account bought in
  the window and still holds wait for the clear date); a VIOLATION's
  last rescue day reads `sell-by:…,+0d`, not "deadline passed"; a
  written option whose premium was taxed at the write (grant timing)
  shows the buy-back's whole cost as the loss (the engine's inventory
  now carries `recognised_premium`); an option the pipeline renamed via
  ticker.map (TOBASE KGC.US K.TO) is quoted as the contract actually
  held, in its currency; and when the wash-radar reports are older than
  the books (after `run --account`), harvest runs the radar live instead
  of showing a registered-account lock as CLEAR (audit R1-230, R1-232,
  S033-24, S034-11, S038-09).
- **`taxjson-safe-to-sell` reads the wash radar.** Its own position walk
  missed today's (unsettled) buys, booked short covers as long lots and
  ignored ticker renames; quantities and statuses (SAFE, SAFE*,
  FULL-EXIT-ONLY, LOCKED, PARTIAL, VIOLATION) now come from the radar,
  and a bad `--date` is a usage error (audit R1-233, S007-09, S050-03).

- **The wash radar follows the engine's per-holder superficial-loss
  rule.** A loss is superficial only for units a holder — your taxable
  accounts together, or one registered account — bought inside the
  ±30-day window and still holds. A registered account's shares held
  before the window no longer turn a full taxable exit into a
  "PERMANENTLY denied" EXITABLE/VIOLATION, a registered buyer that has
  sold out no longer LOCKs the name, a new short sale or written option
  is no longer a "recent buy" or a trigger (Canada), a long rebuy now
  triggers a short-cover loss, and a replacement under 0.01 units
  (crypto DCA) still counts. VIOLATION names who must sell what; LOCKED
  says how many shares' loss a sale today would lose and that the
  registered account can still defeat it by selling within 30 days.
  `sell-check` answers PARTIAL (exit 1) when only part of a LOCKED
  position is at risk and ACTION for a violation only taxable shares
  back. Also: an exercised option is no longer booked as a loss on the
  option, a sale executed before a split that settles after it is
  re-denominated like the engine does, own-account registered moves keep
  each account's balance, `country = "usa"` projects get §1091's
  re-short and IRA rules, and an unreadable `taxjson.toml` stops
  wash-radar / buy-check / sell-check / watch instead of treating every
  book as taxable (audit R1-231, R1-232, R1-234, R1-241, S047-09,
  S048-13, S053-14, S053-20, S054-00, S054-03, S054-04, S054-08,
  S054-15, S054-20, S055-01).
- **redact matches the private denylist the way check-pii does.** A
  denylisted number written with spaces or dashes (`1122 3344`) is
  replaced, a denylisted word in the file name is replaced in the output
  name (and counts for `--check`), and a `TAXJSON_PII_DENYLIST` that
  names a missing file stops the run instead of silently turning the
  denylist off (audit R1-345).

- **redact: Québec addresses and accented or ambiguous names.** A
  French-order street line (`1234 rue Saint-Denis`) is blanked like an
  English one; names with accented letters (`Josée Tremblay`) are
  recognised; a name that contains a statement word (`Bill Sample`,
  `Jane Price`) is listed under REVIEW instead of passing silently, and
  a flat CSV's Description column gets the "may still name people" note
  (audit S036-16, S037-05).

- **redact: label cells, other id columns, plan parties.** A Webull-style
  `Account Number / Numéro de compte:,,,,<id>` or `Name:,<name>` preamble
  row has its value replaced (the id survived and the report said "none
  found"); client / plan / portfolio / acct-number columns are id
  columns; annuitant, subscriber, beneficiary and holder/customer-name
  columns are blanked, each cell in its own position (audit R1-341,
  S037-00, S037-02).

- **Web what-if follows the filing.** A native price converts at the
  latest rate on or before the sale date (named with its date when it
  is old), not a hardcoded 1.35, and a currency with no rates is
  refused (audit R1-149). The simulated sale settles like a real one,
  so the superficial-loss window runs on the settle date and a Dec-31
  sale says it is a next-year disposition (R1-197). A Canadian project
  with two or more taxable accounts is simulated on the blended s.47
  pool, as filed (R1-258). A negative quantity buys to cover a short
  position; a sale on a short is refused with that hint (S079-00).
- **Web pages: config, radar and freshness.** A `taxjson.toml` the
  server cannot reload (a mis-cased type, a quoted boolean) is shown as
  an error instead of ignored, and quoted booleans are refused
  (S078-15). A VIOLATION's rescue deadline day reads "sell TODAY", not
  "deadline passed" (S079-06). A wash-radar report older than the books
  (after `run --account`) carries a stale warning on every row
  (S038-09). The "inputs have changed" banner sees the project-root
  maps and compares against the oldest per-account report, like the
  checklist (S078-21).
- **`list --date` says its ACB is per account.** The as-of view
  recomputes each account alone, so a symbol held in two taxable
  accounts shows each account's own cost, not the s.47 blend the return
  uses. The label, `--help` and README now say so (they claimed "full
  ACB fidelity"), and a note names the shared symbols (audit S044-21).
- **Account names that collide with work/ artifacts are refused.** A
  name ending in `_raw`, `_base`, `_gains`, `_wash`, `_tt` (and a few
  other artifact suffixes), or the name `sheltered`, now stops every
  command with a rename hint: `cb_raw` silently vanished from `sum` and
  the estimate, and `margin_raw` overwrote `margin`'s native books
  (audit S022-00, S041-14).
- **`taxjson gains` names an account with no native gains.** After a
  cross-currency rollover skips an account's native books, the run
  deletes the previous run's stale native gains and `taxjson gains` says
  the account is missing and why, instead of omitting it or serving the
  old rows (audit S037-23).
- **The `.sum` TOTAL PROCEEDS / TOTAL COST are labelled.** They are the
  engine's signed figures (shorts and written options negated), not the
  Schedule 3 proceeds and ACB; the report and KNOWN_ISSUES now point to
  `taxjson form-export` for those (audit R1-208).
- **Holdings TOML states its option cost unit.** `cost_per_share` in
  `reports/<account>_holdings.toml` is `total_cost / quantity` (per
  contract for an option, the convention the broker holdings files
  share); the file and README now say so next to `contract_multiplier`
  and the per-share trade prices (audit S030-08).
- **`taxjson-diff` sees hand-reported dispositions.** Rows the pipeline
  moves to `manual_reporting_required` (phantom basis) are compared too,
  so adding, dropping or changing one is no longer "0 added | 0 removed"
  (audit S029-16).
- **No false all-clear from sanity or find-missing-history.** When a
  configured `holdings` file is missing, `taxjson sanity` ends with an
  `INCOMPLETE` line (`"complete": false` in `--json`), `run` prints a
  `!!` line and the checklist keeps the step at attention.
  `find-missing-history` (and `taxjson-missing-history`) no longer print
  "No missing-cost-basis issues found" and exit 0 when a base book failed
  to load or a configured account has no book; they name what was not
  checked and exit 1 (audit R1-324, R1-336, S047-18).
- **A sheltered-account rerun no longer serves stale wash numbers.**
  After `run --account <sheltered>` rebuilt `sheltered_base.json`, `sum`,
  `form-export` and `close-year` read the older wash-adjusted gains with
  no warning (a registered-account buy that makes a taxable loss
  superficial was missing). They now warn, and `close-year` refuses
  until a full `taxjson run` (audit R1-251).
- **`roc` / `roc-sum` show `distributions.map` adjustments.** The map's
  ACB adjustments are booked only in `<acct>_base.json`, so both views
  said there were none while the engine applied them; they are listed now
  (MAP_ROWS), and `roc-sum` warns when the same symbol and date also has a
  `.tt` ADJUST (the ACB would be reduced twice) (audit R1-163).
- **`reports/ccd.rpt` and `leaps.rpt` match their query twins.** They
  counted phantom-basis (tainted) rows that `ccd-sum` / `leaps-sum` skip,
  `leaps.rpt` was titled "LEAPS" while listing every long option of any
  tenor (now titled so, pointing at `leaps-sum`), and an unreadable input
  printed TOTAL 0.00 with exit 0 (now exit 1) (audit R1-173).
- **`scan` US-LISTING respects DISTINCT and the US line's own dividends.**
  A US stock was called a "Canadian issuer held via its US listing" when
  any `.TO` symbol shared its root, even one `ticker.map` declared
  `DISTINCT` (a CDR), and a non-paying US line borrowed the `.TO` line's
  dividends. The check now needs the US line itself to pay, and a
  `DISTINCT` ruling silences it (audit S042-06, S049-09).
- **Query views stop on an unreadable file.** `list`, `shares`, `winners`,
  `wash-sales`, `ccd-sum`, `leaps`, `gains`, the transaction views and the
  -sum roll-ups warned about a truncated work/ file and printed a partial
  report with exit 0 (`winners` moved by 39k, `scan` said "clean scan");
  they now name the file and exit nonzero (audit S045-01, S042-05).
- **An unparseable `taxjson.toml` is an error in every query command.**
  It was read as an empty config: `fees-sum` converted a USD-base
  project's fees to CAD at an invented 1.35 and dropped its sibling-account
  guard, and the radar treated registered books as taxable (audit S049-00).
- **`divs-sum` and `winners` separate registered accounts.** Both summed
  RRSP/TFSA/LIRA/RESP amounts into the headline (the 2025 `winners` total
  was 2.24x the Schedule 3 gain, and the USD `divs-sum` 2.25x the slips);
  they now print TAXABLE and SHELTERED lines, and the checklist points the
  slip tie-out at the TAXABLE line. `divs-sum` counts DIVIDEND rows only
  (payments in lieu are `dil-sum`; adding the two counted PIL twice), and
  the `divs` help says it shows both (audit S040-13, S041-04, R1-272).
- **`winners` and `ccd-sum` count routed phantom-basis sales.** The
  "skipped N tainted" warning read only in-line rows, so a pipeline file's
  `manual_reporting_required` sales vanished from the ranking without a
  word (audit S040-15).
- **Tax-year views follow the settlement date.** `winners`, `gains`,
  `ccd-sum`, `leaps` and `leaps-sum` windowed a tax year on the trade
  date, so a Dec-31 trade that settles in January dropped out of every
  year's view (the 2026 `ccd-sum` was 3,000.97 short of `ccd.rpt`). On a
  settle-basis project the year window now uses the settlement date, like
  `sum` and form-export (audit R1-171, R1-186, R1-238, R1-273).
- **`leaps` / `leaps-sum` warn outside the books' year, and the default
  window refuses a stale build.** They had no scope warning (`leaps-sum
  all` said "all history" over one year); `winners`, `ccd-sum`, `leaps`
  and `leaps-sum` with the default window now stop when `work/` was built
  for another year than `[settings] year` instead of saying the year had
  no dispositions (audit S048-11, S048-14).
- **`taxjson fees` counts a charged fee as a fee.** Standalone FEE rows
  (IB market-data subscriptions, Questrade/RBC custody fees) were shown
  negative and netted against commissions, and a commission refund was
  shown as a charge; FEE rows now follow the repo convention (positive =
  charged) (audit R1-54, R1-269).
- **`taxjson trades` prints signed totals and fees.** A fee rebate printed
  as a charge and a penny close's negative proceeds as positive, so the
  single-account taxtext did not round-trip; TOTAL SELL and `trades-sum`
  sold now net those proceeds signed (audit S039-11).
- **Webull: a Proceeds cell that does not fit its trade is refused.** The
  gap between Proceeds and quantity x price is the commission; one that is
  negative or far beyond commission size (a shifted or mislabelled
  column) now stops the parse naming the line, as Questrade, IB and RBC
  already do — it used to book with only a schema warning (S023-19).
- **Generic importer: a sell whose commission exceeds its gross nets
  negative.** A penny option close with a larger commission is booked with
  its negative net (the schema accepts it since S017-00; the engine deducts
  it) — it used to be clamped to 0 without an amount column, losing the
  excess commission, or refused as a mis-mapped column with one (S057-02).
- **Webull: exercise/assignment is inferred only on evidence.** A $0 option
  close is paired with a stock trade at the strike only when that trade
  carries Webull's $1.00 exercise/assignment charge; a limit order at the
  strike after a worthless expiry keeps the expiry's gain or loss (it used
  to fold silently into the shares' cost). Every inferred pair is named on
  stderr, a rejected candidate is a warning, the closest option wins
  whatever the row order, the row's own `@Symbol` names the underlying,
  and a Dec-31 assignment settling in January pairs across the two
  yearly exports (R1-15, R1-94, R1-175, S066-12, S066-02, S065-24).
- **Webull: no trade row is dropped silently.** A BUY/SELL row with a
  blank or unknown currency, or a blank Date, is refused naming the line;
  `usd`/`Sell` spellings are read; a header with two columns matching one
  field (an inserted `Gross Proceeds`) is refused; a cell spanning lines
  (an unescaped quote that swallowed the next rows) is refused. Two
  symbols sharing one Security Description, the new one opening with a
  sale, are named as a likely ticker change with the `ticker.map` line to
  add (R1-92, R1-97, R1-98, S066-04, S066-10).
- **Docs: Webull income.** The Webull Trading Summary carries no income;
  README and KNOWN_ISSUES say so and document the `.tt` `INTEREST` line
  for T5 interest (R1-96).
- **`.tt` lines: canonical symbols and fewer false alarms.** A symbol is
  upper-cased on read (`aapl.us` was its own pool and the broker's sale
  went short with no gain), and a suffix that is not a market (`XYZ.TSX`,
  `XYZ.CA`) is a warning naming the line. Futures totals are no longer
  called typos (the 1/100 contract-size guess), two identical `ACQUIRED`
  lots arriving the same day no longer collapse into one arrival leg (a
  phantom 100 shares, or a refused `AmbiguousTransferDateError`), and a
  hand-entered split of a USD stock no longer skips the holdings refresh
  with a "cross-currency rollover" message.
- **json → .tt keeps the year a sale settles in.** `taxjson-convert-tt`
  (json to tt) and the single-account `taxjson events` view wrote the
  trade date; a `.tt` line has one date, read as the settlement date too,
  so a Dec-31 sale settling in January moved into the earlier year on
  re-import. They now write the settlement date on a settle-basis project
  (`--date-basis trade` for the converter), count the rows whose dates
  differ, keep a Questrade-style `commission` as the fee, and warn about a
  contract multiplier the format cannot carry.
- **Generic importer: fewer ways to book wrong money quietly.** Symbols
  are upper-cased (`xyz` and `XYZ` used to be two pools, the sale a
  phantom short). An unmapped action that carries a quantity or an amount
  (a DRIP reinvest, say) is an `UNBOOKED` warning — on the console, fatal
  under `run --strict`, a `--lint` failure — instead of a note in the
  report. A row with no price is still checked against its amount (a
  swapped fee/amount mapping booked the commission as the cost), a
  mapping with no fee column infers the commission from the net instead
  of refusing a valid commission-inclusive export, a futures symbol needs
  its amount (the contract size is never guessed as 1 or 100), and an
  unescaped quote that swallows the next row stops the import naming the
  line.
- **Standalone fee rows have one sign: positive = charged.** The generic
  importer's `fee` action booked a CSV's negative cash as a negative fee
  (the opposite of IB, Questrade and RBC, so `fx-cash` read a charge as
  cash received); `[formats] fee_sign` picks the CSV's convention. The
  `taxjson fees` view flipped every IB/Questrade/RBC fee row into a
  rebate and understated TOTAL FEES; it now reads them as charged.
- **Overlapping Kraken ledger exports are fine.** Two ledger exports in
  the crypto folder that repeat rows (an all-history ledger beside the
  yearly ones, a copy) stopped every run with a misleading "check that
  the ledger export belongs to this account" error, and a ledger file
  with repeated rows doubled its instant trades without a word. A
  ledger row is now counted once per txid; the same txid with different
  content stops the run naming the files. A Kraken row with fewer or
  more cells than the header (a missing `fee` read as 0) is refused.
- **Kraken rows the parser cannot book are UNBOOKED warnings.** An
  airdrop, forced conversion, adjustment, sale, credit, Earn migration
  or margin/rollover/settlement row used to hide behind a note saying
  transfers "don't affect gains"; an instant-trade spend with no
  receive leg (or the reverse) was a warning only in the report. Both
  now print `warning: UNBOOKED:` on the console, and `run --strict`
  refuses them. A trades fill with a nonzero `margin` value warns that
  it is booked as spot.
- **One crypto symbol per coin.** Lower-case codes (`eth/usd`, `dot.s`,
  Coinbase `sol`) are upper-cased instead of opening a second pool (a
  lower-case Kraken pair became a swap with a phantom `usd` coin);
  Kraken's bonded staking codes (`DOT28.S`, `KSM07.S`, `SOL03.S`, ...)
  fold to the bare coin; Coinbase `ETH2` folds to `ETH` as Kraken's
  already did. Swapping ETH for ETH2 (a Coinbase Convert, a Kraken
  `ETH2.S/ETH` fill or ledger wrap) is a counted non-event instead of a
  sale at market value.
- **Coinbase refuses what it cannot read.** A blank `Price Currency`
  cell (the column present) no longer defaults to USD — a CAD row was
  converted twice; a header written with spaces after the commas is
  recognised; a cell that spans lines (an unterminated quote that
  swallowed the rows after it) or a row wider than the header stops the
  parse naming the line.
- **A crypto-to-crypto swap has one value.** Both legs of a swap (Kraken
  crypto pairs and instant trades, Coinbase Convert without a Subtotal,
  Advanced Trade on a crypto pair) used to be priced from each coin's
  own daily close, so the spent coin's proceeds and the received coin's
  cost differed — a phantom gain or loss. A Kraken instant trade with
  `amountusd` now uses it for both legs, and `taxjson-fill-crypto`
  values both legs at the received coin's fair value (the spent coin's
  when the received one has no price).
- **`taxjson fetch` covers the superficial-loss window and keeps
  tokens out of error messages.** The default Questrade window for a
  tax year now runs Dec 1 of the prior year through Jan 31 of the next
  (was Dec 15 .. Jan 15): a repurchase on Jan 16-30 or Dec 1-14 in a
  fetch-only account (an RRSP buying back what the margin account sold
  at a loss) was never seen, so a permanently denied loss was allowed.
  An activity with no `netAmount`/`grossAmount`/`commission` is written
  with a blank cell, so the parser refuses it instead of dropping a
  dividend as a "zero-net" row. A refused redirect no longer prints the
  redirect URL's query (the Flex token or Questrade refresh token). The
  live-holdings snapshot takes an option's suffix from the account's
  books when they hold that contract, so a CDR such as AMZN.TO no longer
  makes the account's US AMZN option look Montreal-listed (a false
  verify/sanity mismatch).
- **taxjson-merge never emits a partial merge.** The legacy merge that
  builds the crypto books, the blended base, `sheltered_base.json` and
  the audit tie-out printed "cannot read" and exited 0 with the
  unreadable file's rows missing — `run --fast` over a damaged cached
  Coinbase book dropped half the crypto gains with a clean console. A
  missing or unreadable input is now an error (exit 1, nothing on
  stdout), as in taxjson-merge2.
- **ticker.map means one thing everywhere.** Rule symbols are
  upper-cased on load (a lower-case rule used to rename nothing while
  `taxjson scan` called it live), a BOM and inline `# notes` are
  stripped, and renames chain to their end (`GLOBAL OLD.US NEW.US` plus
  `TOBASE NEW.US NEW.TO` now sends OLD.US to NEW.TO instead of splitting
  the pool into a phantom short). `taxjson run` refuses a map with a
  rename cycle, one symbol renamed to two different targets, or a
  `DISTINCT` pair that the renames pool together. Scan's MAP-UNUSED
  note follows chains and judges a rule the way the engine applies it:
  a suffix-less `GLOBAL QQOL QQNW` that matches only `QQOL.US` is
  reported with a hint to write the suffixed form. A malformed
  `ticker_extraction_overrides.txt` line now stops the run by
  file:line instead of being skipped.
- **Filing commands refuse partial or broken books.** A taxable account
  whose stage failed (inputs but no books) now stops `form-export`,
  `t1135`, `close-year` and `reconcile-slips` instead of a one-line
  warning with the account left out. A truncated taxable gains file stops
  `sum`, `estimate`, `instalments`, `t1135`, `form-export` and
  `close-year` with the file named (no traceback); an unreadable native
  file stops `fx-cash`, and `sum` says why the line-15300 FX note is
  missing. `sum`, `estimate`, `form-export`, `t1135`, `carryover` and
  `check-filed` warn when the books are not the clean result of the
  current inputs (validation errors, pending elections, inputs changed
  since the last full run); `close-year` refuses such books without
  `--force`, and refuses per-account (unblended) books from
  `run --account`. The checklist counts one validation error once.
- **instalments passes the estimate's warnings through** (unreadable or
  other-year books, excluded tainted sales) and prints the rate-vintage
  note for a year before the built-in tables.
- **tax_date is honoured everywhere.** `form-export`, the FOR THE RETURN
  block, `carryover` and `t1135` use the project's `tax_date` (a
  Canada project on "trade" dropped a Dec-31 sale settling in January).
- **carryover flags a locked year it disagrees with** (and the moving
  option_grant_timing_since default).
- **t1135:** an assigned written put's premium reduces the shares' cost
  and an exercised call's cost is added; same-timestamp rows follow the
  engine's order (buy before sell) so the threshold test no longer depends
  on file order; a split inside a trade's settle lag no longer strands
  cost; `t1135.map` survives a BOM, follows a ticker change and names an
  override that matches nothing; denied superficial losses missing from
  the cost columns are named. Books built for another year are warned
  about. The other user map files (distributions.map, yf_ticker.map,
  crypto_ticker.map, security overrides) read a BOM too.
- **audit:** `--year` for another year and `--all-years` no longer call
  fresh books stale (the saved gains files hold one year); a crypto fee
  priced by the fill stage ties out; a large book's per-row 4-dp rounding
  no longer fails the totals.
- **`crypto = "false"` (quoted) is refused** by every command instead of
  moving an equity book to the crypto line.
- **The account .sum lists phantom-basis sales** in a MANUAL REPORTING
  section. `option-boundary` applies phantoms.json (a phantom long option
  sold to close is not a write). `wash-sales --explain` traces the
  blended books the table comes from.

- **reconcile-slips reads real slips.** A blank proceeds cell beside a
  cost (an option that expired worthless) is nil proceeds, and a
  worthless expiry with no slip row no longer fails the check. A written
  option booked twice under grant timing counts its contracts once. Slip
  symbols go through the project's ticker.map (KGC ↔ K.TO), and broker
  option descriptions, share classes (`BRK B`), UTF-16 files, French and
  T5008 box headings are read. Two listings of one root (AMZN.TO and
  AMZN.US) are no longer folded together. A row with amounts but no
  symbol, or an unreadable quantity, counts as not reconciled; a second
  column that also looks like quantity/proceeds/cost is refused instead
  of silently taking over. Books built for another tax year are refused
  with a rebuild message instead of "dropped CSV rows".

- **RBC books what it used to drop, and says what it cannot book.** A
  stock dividend (`DIS - ... STK DIV ON N SHS`) enters at $0 cost like
  Questrade's; an RBC Dominion Securities "Reinvest @ $p" distribution
  row with units books the income and the purchase; a reversed DRIP
  cancels its original; a reorganization whose legs or cash-in-lieu
  straddle two yearly exports is paired across them; a split on a short
  position scales it up; an option strike with a thousands separator
  (`5,025`) is read whole; a cash-in-lieu reversal nets instead of
  adding proceeds. Rows it cannot book (unclassified, unmatched legs,
  a Transfers row with no quantity) are `UNBOOKED` warnings on the
  console and fatal under `run --strict`.
- **RBC refuses rows it would mis-book.** A trade with a blank Value
  (read as $0 proceeds or cost), a Value that does not fit quantity x
  price (a commission below zero or far above RBC's), a Description with
  an unescaped quote that swallowed the next row, and a listed-ticker row
  whose text names a contract on another underlying are errors. A stock
  leg whose text quotes the assigned contract books as the stock; a
  security named DELIVERY... no longer turns a transfer-in into a
  transfer-out; FRACTYL-style names keep their cash-in-lieu of dividend
  as income; a blank Description falls back to the Symbol Description
  for security overrides; a rights/warrant expiry settles on its date; a
  blank-settle assignment's option leg shares its stock leg's cycle; a
  notional distribution warns that its income is not in the totals.
- **Broker-marked shorts are not missing history.** A position RBC
  marks as a short sale (`SHORT.` / `COVER SHORT.`) is no longer offered
  as a phantom by `find-missing-history`, `--suggest-phantoms` or the
  run's go-short hint; `find-missing-history` lists it apart.
- **Questrade keeps a TSX listing traded in USD on `.TO`.** `DLR.U.TO`
  and `XUS.U.TO` bought in USD became `DLR.U.US` / `XUS.U.US`: a pool
  apart from the same units at RBC or IB, a `JOURNAL DLR.U.TO DLR.TO`
  rule that never fired, and a Canadian ETF listed as US property on the
  T1135. A `.TO` symbol now keeps `.TO` whatever the row currency, so the
  API (`FNV.TO`) and web (`.FNV`) spellings of a USD dividend agree; a
  CAD dividend or ROC on a US stock bought from the CAD side (`EXCHANGE
  RATE`) now reaches the `.US` pool instead of a phantom `.TO` one.
- **Questrade learns identities from all of an account's exports.** A
  dividend, ROC, stock dividend, DRIP or cash-in-lieu row under an
  internal code (`A020626`) whose trade sits in last year's export stayed
  on the code, and a ROC there became a capital gain. The
  description map now spans every export of the account; the event
  wording (`STK DIV ON`, `STK SPLIT ON`, `REINV@C$`, `CASH IN LIEU OF`)
  and Interactive Brokers' transfer wording no longer block the match;
  a code nothing resolves is a warning (and a `--lint` finding) with the
  `ticker.map` line to add, and a spinoff chain booked under an internal
  code is flagged at the election step.
- **Questrade flags a ticker change with no corporate-action row.** A
  symbol that stops with shares open while another with the same
  description opens with a sale they cover gets a warning with the
  ready `GLOBAL old new` line (the sale's gain used to drop out).
- **Questrade row shapes.** `BUY`/`buy` and lower-case symbols are read
  like `Buy`; an unknown action that moves shares is an `UNBOOKED`
  warning on the console (fatal under `run --strict`); a `BRW`
  Norbert's-gambit journal between `DLR.TO` and `DLR.U.TO` is booked as
  a TRANSFER pair like RBC's journal legs, carrying the stated book
  value (skipped before, leaving the units on `DLR.TO`), which a
  `JOURNAL DLR.U.TO DLR.TO` rule nets; a negated stock-dividend row cancels its original; a DIS or
  stock-dividend row carrying both shares and cash is refused; a
  CAD-settled US trade's CAD net must match the USD gross at the stated
  rate and keeps its sign; a blank settlement date on an option is T+1;
  a transferred option books under its OCC symbol at 1/100 of the book
  value per share; a stock leg whose description quotes the contract
  books as the stock; a split on a short position scales it up; a
  warrant expiry books on the date in its description; a transfer row
  carries its description, so `--security-overrides` reaches it; the
  no-book-value warning names the remedy that works (a `.tt` BUYSELL).
  A Questrade DIS chain that removes units is an `UNBOOKED` warning.
- **An IB corporate-action cancellation reaches the other statement.** A
  split booked in one yearly statement and cancelled (`Ca`) in the next
  is undone when both are in the account's inputs; it used to stay
  applied with a "reverse it by hand" warning (audit S059-04).
- **Generic importer: one spelling per security.** `BRK-B` and `BRK/B`
  are `BRK.B` (they were separate ACB pools, so a cross-account
  superficial loss was missed), an OCC symbol padded to 21 characters is
  compacted, and an option description in the symbol column is refused
  instead of booked as a share that never expires (audit S010-04).
- **US projects: an IB return of capital reduces basis by default.** A
  `country = "usa"` project no longer books an issuer-designated return
  of capital as a dividend under Canada's s.90(2) rule; it is a
  nondividend distribution (IRC s.301(c)(2)) unless `[settings]
  foreign_return_of_capital` says otherwise (audit S013-01).
- **Settle dates that pair across legs and files.** An IB assignment's
  option leg now settles with its stock leg (before the 2024 T+1 cutover
  the option settled a day earlier, and a same-day trade could consume
  the premium), and a Dec-31 0DTE option trade is clamped to its expiry
  even when the expiry row sits in the next yearly export (audit S058-01,
  S055-22).
- **IB security identity across statements and issuers.** Income is
  moved to the held listing of its ticker only when the ISINs match (an
  AT&T dividend no longer lands on Telus `T.TO`), and the holding may
  come from the account's other statements (a ROC-only statement no
  longer books a gain on a phantom listing). Option-root aliases are
  learned from all of the account's statements, and after a ticker
  rename the canonical root is the contract's underlying (SQ -> XYZ). A
  USD trade of a TSX `.U` unit is `X.U.TO`, as RBC books it. One stock
  listed under two symbols (a ticker change) is an ATTENTION line
  naming the `ticker.map` GLOBAL rule that joins them (audit S059-24,
  S060-19, S060-00, S059-15, S059-11, S010-06, S059-13, S060-17).
- **IB corporate actions nobody books are loud.** An unhandled Corporate
  Actions row is now an UNBOOKED warning (console; fatal under `run
  --strict`) that points at a `.tt` booking instead of a manual TRANSFER
  row (dropped in taxable accounts). The same goes for an option or
  futures contract adjustment (it used to become an equity SPLIT on an
  invented symbol), a spin-off debit on a short parent, and a tender
  whose allocation delivers another security (a share-for-share exchange
  offer was reported as a no-op). An IB stock dividend books its shares
  at $0 cost with a console note, like Questrade's; an Options
  Expirations row keeps its own asset category (a futures-option expiry
  closes the `F:` position) (audit S058-16, S058-22, S058-24, S013-06,
  R1-56).
- **IB statements that do not cover the year are reported.** `taxjson
  run` now says on the console when an account's IB statements stop
  before Dec 31 of a finished year (the 2024 statement ending Dec 27) or
  leave a gap between two statements, and when a statement has no Cash
  Report to reconcile against (audit R1-2, R1-195, R1-53).
- **IB rows the parser cannot book are refused, not counted.** A Trades
  row in an asset class without a parser branch (Bonds, Mutual Funds)
  that moves money, and a security Transfers row with a blank Qty, stop
  the parse naming the row. An execution-level `Trade` row is skipped
  only when an `Order` row covers the same symbol and day (a Trade-only
  fill of another symbol was dropped, a Trade row before its Order row
  was doubled); levels that disagree are refused (audit S060-08, R1-55,
  S060-10).
- **A penny option close no longer stops `taxjson run`.** A sell whose
  commission exceeds its gross (closing at 0.01) nets negative proceeds;
  the schema used to refuse it on the always-strict parse, although the
  engine books it. An assignment stock leg's money is now checked
  against quantity x strike like any trade (audit S017-00, S017-02).
- **Comma-grouped numbers read right.** `0,125` is refused as a decimal
  comma everywhere (it read as 125); an option strike written `5,000.00`
  keeps its thousands (it was cut to strike 5); an IB `Split 1 for
  1,000` is 1-for-1000 (audit S055-08, R1-170, S058-19).
- **UTF-16 exports are routed.** Broker detection decodes a UTF-16 BOM
  the way the IB, Questrade and RBC parsers do (audit R1-70).
- **Security overrides are stricter and reach every row.**
  `ticker_extraction_overrides.txt` keys now match whole words (on IB,
  where the description is the bare ticker, `BN` no longer rewrites
  ABNB or BNTX), never rewrite option or futures rows, take the currency
  case-insensitively, ignore a leading BOM, and a malformed line fails
  the parse naming its line number instead of being skipped. The
  override now runs before TRANSFER rows are set aside (the custody
  sidecar gets the corrected symbol), IB TRANSFER rows carry the
  security (`ACATS (DLR)`) so the override can match them, and an IB
  split of an overridden security stays a split (its `symbol_new`
  follows the rewrite) (audit R1-143, S001-00/01/02, S012-09, S027-01,
  S059-03).
- **Transfer sidecar is de-duplicated across overlapping exports.** A
  re-downloaded or overlapping statement no longer doubles every custody
  row in `taxjson transfers` or moves twice the shares in the holdings
  evidence netting (audit S026-23, S027-00).
- **Corporate-action elections say what the law says.** The s.85.1
  share-for-share rollover is automatic when it applies (you opt out by
  reporting the gain); taxjson described it backwards and printed a
  FILING REQUIRED reminder for a form that does not exist. The option
  text now says when s.85.1 applies (not to a Canadian company bought
  by a foreign purchaser) and the reminder is gone for it. An s.86.1
  spin-off now takes the allocated cost in CAD (`--hint
  allocated_acb_cad=`, the parent's CAD ACB times the spin-off's share of
  the combined FMV, s.86.1(3)); the old source-currency `allocated_acb`
  still works but warns, since converting it at the spin-off date moved
  FX drift between the pools. A misspelled hint in a hand-edited
  `manifest.json` (`fmv`, `allocated_ACB`) now stops the run by name
  instead of booking $0, and a taxable merger at $0 FMV warns on every
  run even when cash-in-lieu was paid.
- **IB mergers are never dropped half-way.** A merger whose out-leg and
  in-leg sit in two statements (a year-end event split across yearly
  downloads) or carry Date/Times a day apart was skipped with no warning:
  the old shares stayed and the new ones never arrived. The legs are now
  paired across all of the account's statements and within a week; a
  leg with no partner stops the run as an unsupported event naming it.
  A merger held in both the TSX and NYSE listings becomes one event per
  listing (the second listing's shares were never converted), a merger
  of a short position is refused instead of booked as `OLD -> OLD`, and
  a cross-listing journal listed before its merger is folded once
  instead of also becoming its own election.
- **Questrade spin-offs land on the parser's symbols.** A dotted target
  (`ABC.WS`, a class share) was left without its market suffix and a
  Venture `.VN` listing became `ABC.VN.TO`, so the spun-off lot and its
  later sale sat in two pools (a phantom long, a short sale, no gain).
  The extractor now uses the parser's own suffix rule, reads a padded or
  UTF-16 header like the parser (the whole spin-off used to vanish), picks
  an interlisted parent by the listing held on the spin-off date (never
  by file order; ambiguity is refused with a warning), and nets a DIS
  chain whose rows straddle two yearly exports.
- **RBC spin-off parents and merger sources resolve to the right pool.**
  The parent took its market suffix from the spun-off shares' row (a TSX
  parent became `.US`), could resolve to a covered call's option code or
  to another company sharing its first word (`BROOKFIELD CORP` →
  Brookfield Renewable), and an interlisted company was resolved by
  file order. A spin-off's s.86.1 ACB reduction then landed on an empty
  pool as a phantom gain. The parent now uses its own listing, options
  never qualify, a fuzzy match needs both names to agree, a merger's
  temporary code prefers the removal's currency, and an ambiguous
  listing is refused with a warning. A spin-off debited from a short
  parent is refused instead of booked as a long buy and a negative
  dividend.
- **RBC reorganizations pair on evidence.** A ratio written with a
  thousands comma (`1 FOR 1,000`, `1 NEW = 1,000 OLD`) was read as
  1-for-1, so the cash-in-lieu row sold almost the whole position. Two
  option contracts adjusted the same day now pair by the closest strike
  (they swapped ACB by file order); a removal no longer pairs with a
  lone unrelated receipt or a half-name match; a company named "ROC ..."
  no longer turns merger cash into a return of capital; an exchange into
  a different company worded `XCH TO` asks for the merger election
  instead of rolling over silently; and a merger of a short position is
  refused loudly instead of renaming the new ticker into the temporary
  code.
- **`distributions.map` rows find their shares.** A map whose first line
  carried a byte-order mark, a lowercase symbol, a key naming the
  listing a `ticker.map` rule consolidates, or the old ticker after a
  ticker change was skipped as "no shares held" (or booked on a dead
  pool) and the ACB increase was lost. Keys are now matched
  case-insensitively, through `ticker.map`, and onto the ticker live on
  the record date; a sale executed before a split but settling after it
  no longer inflates the record-date balance. The NOTE also says the
  distribution is income to report from the T3/T5 slip.
- **Filing checklist fixes.**
  - run-clean compares the inputs with what the last full run was built
    from (content, not dates): a deleted input, a corrected export copied
    with its old date, or a new `distributions.map` / `phantoms.json` /
    `ticker_extraction_overrides.txt` now says "inputs changed". It also
    flags an account with inputs but no report (a run that died on it, or
    a `run --account` of another account).
  - wash-reviewed counts a December loss that settles in January in the
    settle year, like every other denial total.
  - t5008 reconciles all slip files together (one per broker is normal;
    each file alone could never reconcile), finds `.CSV` files, reads
    only `inputs/slips/`, and names a non-CSV file there as not
    reconciled. `taxjson reconcile-slips` takes several slip files.
  - form-export's gain check allows for per-row rounding on a big book
    (a 6-cent residual over 831 rows was a false attention).
  - A done mark no longer hides a detector that crashed or had nothing
    to check. An unreadable `checklist.json` is reported by name and never
    overwritten. Changing `year` no longer brings last year's marks back
    at the next mark.
  - The fees step no longer sends trade commissions to line 22100 or
    calls data subscriptions deductible: line 22100 is the margin
    interest from the statements. The carryover step says what to record
    (Canada: the 100% loss; US: the Schedule D line 21 deduction).

- **check-filed recomputes a locked year the way it was filed.** The
  drift check used the current project's written-option timing, so a
  2026 project without `option_grant_timing_since` recomputed the 2025
  lock on close timing and advised amending a correct return. It now
  uses the timing the lock recorded and notes when the project differs.
- **One bad lock no longer switches off the drift guard.** A lock that
  cannot be read (bad JSON, a hand-edited file missing a count) made
  `run --strict` exit 0 and skip every other lock, and made
  `check-filed` crash with a traceback. Each lock is now checked on its
  own; a bad one is named and fails `--strict`.
- **A renamed account is reported, not recomputed from its old book.**
  A locked account that is no longer a taxable account in
  `taxjson.toml` was recomputed from its orphan `work/<name>_base.json`
  and the check said OK.
- **The lock records dividends and payments in lieu separately.** A
  dividend reclassified as a payment in lieu (different return line, no
  gross-up) now shows as drift; locks written before this keep
  comparing the combined income only.
- **`country` and `tax_date` are checked for every command.** "Canada",
  "CA" or " canada" are read as `canada` everywhere (check-filed and the
  web what-if crashed on them, switching the drift guard off); an
  invalid `tax_date` stops every command with a clear message instead
  of an engine usage error. The checklist no longer reports a failed
  check-filed as drift.
- **reconcile-slips counts phantom-basis sales.** A sale reported by
  hand (phantom cost basis) was still shown as MISSING_FROM_COMPUTED;
  it now matches the slip with the "phantom basis included" note.
- **Canada estimate keeps its sign.** Eligible dividends at a low
  bracket can lower the tax on the other income; the estimate (and the
  AMT total and average rate) now shows that as a negative figure — a
  saving — instead of flooring it at 0.00 under a `WITH - BASE` trace
  that was negative (R1-47).
- **Unused foreign tax credit reaches the province.** Foreign tax the
  federal tax cannot absorb is credited against provincial tax (form
  T2036, limited to provincial tax x foreign income / net income)
  instead of being dropped; the estimate and the current-year
  instalment basis were overstated for foreign-dividend-heavy, low-tax
  years (S077-16).
- **Estimate discloses what it leaves out.** A NOTE names the headroom
  a prior-year minimum tax carryover (T691 Part 8, line 40427) could
  use when AMT does not bind — the carryover is not modelled
  (KNOWN_ISSUES); the assumptions line no longer sends interest to
  views that never show it (R1-218, S078-00).
- **option-boundary: an open write expiring later this year is open.**
  A written option whose expiry is after today is an ordinary open row
  (premium recognised in the write year), not "expired … the broker
  export is missing it"; the checklist no longer turns `[!]` for it.
- **option-boundary follows renames and keeps the sign.** A ratio-1
  rename SPLIT of an option carries the written lot to the new symbol,
  so its buy-back is matched; a write whose commission exceeds the
  premium shows a negative premium, as the engine books it.
- **option-boundary: cash-settled index options are not folded.** An
  assignment whose underlying never trades as stock in the account
  (XSP, SPX) is reported as a cash settlement — premium in the write
  year, settlement loss in the close year, no T1-ADJ — instead of
  advising the removal of a premium the engine keeps.
- **option-boundary reads a lock's close timing.** When `filed/<year>.json`
  records close timing but this project puts that year's writes on grant
  timing, an expiry, buy-back or still-open write is ATTENTION (the
  premium is in no return: set `option_grant_timing_since` to the next
  year, or T1-ADJ to add it), and an assignment says no amendment is
  needed instead of "remove the premium … filed with it".

- **carryover says what its numbers leave out.** The ledger now notes
  that slip capital gains (lines 17400/17600; US Schedule D line 13) and
  the line-15300 FX gain on foreign cash are not in NET GAIN(LOSS), flags
  rows before the project year as rebuilt from this project's books and
  possibly partial, and `--claimed` / README state the units to record
  (Canada: the 100% loss, line 25300 x2 at 50%; US: Schedule D line 21).
- **claimed_losses.txt takes `1,234.56` and `$1,234.56`.** An amount
  written the way the return or notice of assessment prints it was
  dropped with a warning, so the carryforward kept the claimed loss. A
  malformed grouping is still refused.
- **carryover: an old claim no longer eats a later loss.** A
  `claimed_losses.txt` amount recorded for a year the books show no loss
  for was held and taken by the next loss in ANY later year, lowering
  its carryforward. A loss carries back only 3 years (ITA 111(1)(b)), so
  a claim left unmet past that window now stays unmatched, with a
  warning, and the carryforward is untouched.
- **fx-cash counts assignment cash, and only cash.** An option
  assignment's stock leg booked as `ASSIGN` (Webull) now spends or
  receives currency like a trade; crypto-for-crypto swap legs (Kraken,
  Coinbase Convert), staking rewards paid in a coin, and stock-for-stock
  corporate actions no longer count as foreign cash moving (they
  invented thousands of dollars of s.39(1.1) gain or loss).
- **fx-cash says the figure can be wrong either way.** The report (and
  the `sum` FOR THE RETURN line and its `--json`) now carries the
  overdraft count and a caveat that unseen conversions and deposits can
  move the estimate in either direction — it used to say only that it
  "understates activity" — and the report shows the ledger's foreign
  cash at Dec 31 to compare with the brokers' balances.

- **Instalment interest on the least amount due by each date.** ITA
  161(4.01) deems the requirement on each due date to be the least
  cumulative amount any method (current-year, prior-year, CRA reminder)
  calls for by that day. `taxjson instalments` priced each method as a
  whole year and took the cheapest, which overstated interest and the
  s.163.1 penalty whenever the cheapest method changed between dates.
  The report now shows the mixed schedule when no single method
  governs.
- **Instalment rates for 2023 and a flag before the table.** The
  built-in CRA overdue-tax rates now start with 2023 (Q1 8%, Q2-Q4 9%).
  A 2023 year was charged 2024's 10% all year and the report called it
  the published rate; a year before 2023 now says its days ASSUME the
  earliest rate (`rate_extrapolated`).
- **One low prior year no longer waives instalments.** With only one
  of `prior_year_net_tax` / `second_prior_net_tax` set and at or below
  $3,000, `taxjson instalments` said "No instalments required" and
  printed the unset year as 0.00. Both preceding years must be at or
  below $3,000 (ITA s.156.1(1)); with one unknown the test is now
  unknown (instalments assumed required) and an unset year prints as
  "not set".
- **Instalment interest keeps compounding after you catch up.**
  `taxjson instalments` now computes interest the way CRA publishes it:
  interest on each required instalment from its due date, minus
  interest on each payment from its date (or January 1), both to the
  balance-due date and compounded daily. The old running balance
  stopped compounding the accrued charge once payments caught up,
  which understated interest by a few percent and could drop a charge
  under the $25 threshold.
- **Kraken Hybrid Earn moves are yours.** `crypto-sends` classifies a
  Kraken `hybridearnwithdrawal` (the coins move to Kraken's Earn product
  and keep earning rewards) as `self` automatically instead of asking;
  `--set ID=gift|payment` still overrides it.
- **The wash radar sees today's trades.** `wash-radar`, `buy-check`,
  `sell-check`, `watch` and `reports/wash_radar_*` dropped every trade
  that had not settled yet, so right after a loss sale `buy-check` said
  SAFE (and right after a buy `sell-check` said SAFE). A trade now
  counts from its trade date; the windows stay settlement-based.
- **The wash radar takes losses from the engine.** Whether a sale was a
  loss came from the radar's own per-account pool, which missed the
  s.47 blend across taxable accounts, the cost added back by an earlier
  denied loss, and option cost folded in on exercise; real losses showed
  no window and `buy-check` said SAFE. The radar now reads the engine's
  gains files (grant-timed buy-back losses are not wash losses unless
  `option_buyback_loss_superficial` is on).
- **Web what-if prices options per contract.** Selling an option on the
  holding page used qty x price, 100 times too little, and showed a ~99%
  loss. Proceeds are now qty x premium x 100 (the page labels the field
  as the per-share premium and the cost as per contract); a futures
  option is refused rather than priced with a guessed multiplier.
- **Web what-if maps cross-listed options like the pipeline.** An option
  on a `TOBASE`/`GLOBAL`-mapped underlying (a `.US` call booked as
  `.TO`) was looked up under its unmapped name, and the what-if
  simulated writing a new short. It now follows the underlying's rule,
  and a sale that would open a short is refused.
- **`taxjson redact` removes the holder's name from Coinbase and IB
  Flex/HTML exports.** Coinbase's `User,<name>,<id>` line, the `Name`
  column of an IB Flex `Account` section and the Name cell of IB's .html
  statements were kept. IB ids glued to letters (HTML element ids) were
  collected but not replaced, while the report said "every occurrence
  replaced"; they are replaced now, and the report checks the copy and
  lists any id it could not replace.
- **The wash radar applies `phantoms.json`.** Phantom-backed positions
  showed as shorts (rebuys as short covers with invented losses) in the
  radar, `watch`, `buy-check`, `sell-check`, harvest's ADVISORY and the
  web UI, and real violations were missed.
- **One split booked on two dates applies once.** IB and Questrade date
  the same split days apart (KLAC 10:1: 06-11 vs 06-15); both copies
  were applied, scaling the pool by the ratio twice. Copies of one split
  (same security and ratio) within 7 days are one event, applied on the
  earlier date, with a note.
- **IB spin-offs go through the spin-off election.** IB `Spinoff` rows
  were always booked as a dividend at IB's value, with no s.86.1 choice
  (and a Canadian parent's tax-deferred spin-off taxed as income). They
  now ask like every other broker's; the default dividend uses IB's
  value without asking for it. Existing IB spin-offs need one election.
- **Spin-offs use the broker's value.** A deemed-dividend spin-off with
  no (or a 0) `fmv_per_share` hint is booked at the broker's reported
  value when there is one. A taxable spin-off booked at $0 is warned
  about on every run and in the account's .sum until a value is set.
- **IB cash takeovers are booked.** `Merged(Acquisition) FOR USD 30.00
  PER SHARE` is a sale at the cash amount (it was left in inventory with
  only a .sum note). Decimal-ratio and class-share (`BRK B`) mergers are
  parsed; a stock-plus-cash merger stops the run by name for manual
  booking instead of vanishing.
- **A taxable merger uses one value for both legs.** The old shares'
  proceeds and the new shares' cost are now the same fair value (the
  shares received, plus any cash in lieu); the broker's separate out-leg
  and in-leg figures left their difference as a permanent phantom gain
  or loss. Across currencies, each leg is converted at the event date
  (a hint in USD was booked as CAD proceeds).
- **A merger or spin-off held in two broker accounts is booked for
  both.** Two IB sub-account statements, or two RBC accounts, feeding
  one taxjson account kept only the first account's disposition. Their
  quantities are now combined under the one election; an overlapping
  statement of the same broker account still counts once.
- **Questrade spin-off parents are found in any export of the account,**
  and a US parent bought from the CAD side (`EXCHANGE RATE` rows) is
  named with its US listing, as the parser books it. The s.86.1 cost
  reduction used to land on an empty pool and book a phantom gain. RBC
  spin-off parents and merger placeholders get the same all-exports
  lookup.

- **IB: a cancelled trade (`Ca`) nets out.** A Trades row coded `Ca`
  was booked as an ordinary trade, so a cancel-and-rebook was a phantom
  round trip: a loss sale rebooked a cent higher became two denied
  superficial losses and the remaining shares' ACB was wrong. The
  cancellation now drops out with its original fill; when the original
  is in an earlier statement of the same account, `taxjson-merge2`
  (`taxjson run`) pairs them, and a cancellation whose original is in
  no input stays booked with a warning.
- **Questrade: an assignment stock leg keeps its cash.** A row whose
  description said ASSIGNMENT or EXERCISE (or ` - EXPIRED`) had its
  price and cash zeroed even when it carried money: an ASN stock leg was
  booked at $0 cost, a stock named "... EXERCISE EQUIPMENT" vanished, a
  sale of expiring rights lost its proceeds. Only zero-cash option legs
  are zeroed now; an EXP row with cash, or a zero-cash ASN/EX row that is
  not an option, is refused.
- **Questrade: CIL / REI reversals cancel.** A negated cash-in-lieu or
  DRIP row was read with abs() and booked as a second sale or buy (cash
  counted twice, phantom shares). The reversal now removes its original;
  a reversal whose original is not in the file is refused.
- **Webull: a priced trade needs its Proceeds.** A blank, garbage or
  misaligned Proceeds cell on a priced BUY/SELL booked $0 (the whole
  gross became a "fee", so even `--strict` passed). Such a row, a row
  wider or narrower than its header, and a decimal comma are refused
  with the file line.
- **One spelling per Canadian listing.** Questrade's TSX-Venture, CSE
  and NEO symbols (`VVV.VN`, `CCC.CN`, `XYZ.NE`) became `VVV.V`,
  `CCC.CN.TO`, `XYZ.NE.TO`, while IB, RBC and Webull book every Canadian
  listing `.TO`; the pools split and a superficial loss across accounts
  was missed. Every Canadian venue is now `ROOT.TO` in every parser, the
  generic importer and the live-position mapping (`yf_ticker.map` still
  aliases a price lookup, e.g. `PNG.TO PNG.V`). `taxjson-lint-
  crosslistings` warns when one root is held under two Canadian
  suffixes (a `.tt` line or a map rule).
- **Questrade preferred shares are dotted.** `FTN.PRA.TO` is now
  `FTN.PR.A.TO`, as IB, RBC and Webull spell it, so the pools no longer
  split. An existing `GLOBAL FTN.PRA.TO FTN.PR.A.TO` rule is harmless.
- **Decimal commas are refused in the IB diagnostics and slip
  reconciliation too** (open-position, dividend-accrual and slip cells
  were still comma-stripped and read 100x too large).

- **Futures are booked on their settled P/L, not their notional.** Each
  leg's notional (quantity x price x multiplier) was converted to CAD at
  its own date's rate, so the CAD gain carried FX on money that never
  changed hands, and Schedule 3 line 6 showed the notional as proceeds
  and ACB. A futures fill that opens a position now carries nothing; a
  close carries the realized native P/L (commissions included), converted
  at the closing leg's rate. Line 6 shows a gain as proceeds and a loss as
  ACB (the T5008 shape); `sum`, `form-export`, `audit` (which re-derives
  the P/L from the broker rows) and `fx-cash` agree. Owner books: 2025
  +241.09 (CL), 2026 -2,251.16. Options on futures are unchanged.
- **T1135: a futures contract has no cost amount.** A long futures
  position was counted at its full notional (one CL contract added about
  80,000 CAD to the threshold test and could flip "filing required").
  Plain futures are now listed with a nil cost and a note; options on
  futures still count at their premium.
- **`taxjson t1135` applies phantoms.json.** Positions whose early
  history is cut off read as shorts that later real purchases covered at
  zero cost, so those purchases never reached the max-cost or Dec-31
  columns (2024 books: 313k of max cost missing). The report now adds
  the same phantom openings the gains stage adds, and flags a still-held
  phantom "cost understated".
- **One Bank of Canada 404 no longer switches a currency to Yahoo for
  good.** Any HTTP 404 from the Valet API (a maintenance page, a proxy)
  was cached as "series not published" with no expiry, so every later
  run of every project on the machine converted that currency at Yahoo
  closes, cached Bank rates included. Only the Valet API's own
  "Series FX…CAD not found" answer marks a series now; the mark carries
  its date and is re-checked after 7 days; cached Bank observations keep
  their source; the note names the answer and the source used.
- **`taxjson crypto-sends`: is a crypto send a gift, a payment, or your
  own wallet?** Every withdrawal/send that did not arrive on another of
  your exchanges is listed with its fair value (the exchange's spot
  price, or the Yahoo daily close times the Bank of Canada rate) and the
  ready `.tt` sale line. Record the answer with `--set ID=self|gift|payment
  [--note]` (kept in `inputs/<acct>/sends.json`); `--write` generates
  `inputs/<acct>/crypto_sends.tt` with one BUYSELL per gift or payment.
  `taxjson run` asks at a terminal and refreshes the file; the checklist
  has a "Crypto sends classified" step. Stablecoin gifts get the currency
  gain against the USD pool's average cost instead of a sale line. If you
  already declared a send by hand (a `tao_payment.tt`), delete that file
  when you adopt `crypto_sends.tt` — the command warns while both exist.
- **The filed-year lock counts phantom-basis sales.** Its `tainted`
  count read a flag pipeline gains files never carry, so it was always 0
  and a phantom-basis sale appearing or disappearing never showed as
  drift. New locks count them; older locks skip that one comparison.
- **Phantom-basis sales no longer vanish from `form-export`.** Sales
  drawn on `phantoms.json` openings (cost unknown) were left out of the
  Schedule 3 / Form 8949 / TXF output with no warning, and the checklist
  marked the export complete. They are still not in the computed rows or
  totals, but form-export now warns with each one's proceeds, lists them
  in a MANUAL REPORTING section (MANUAL rows in the CSV), and the
  checklist keeps the step open. `t1135` names them too.
- **`distributions.map` counts `phantoms.json` shares.** The record-date
  balance was taken from the book without the phantom openings, so a
  position with pre-window history got too small an ACB adjustment (or
  none, "no shares held"). Removing `phantoms.json` now rebuilds the
  adjusted books under `--fast` too.
- **`phantoms.json` openings reach the superficial-loss context.** An
  opening for a registered (or affiliated) account was applied to that
  account's own report but not to the context the taxable gains are
  tested against, so a TFSA with truncated history looked short and its
  in-window rebuy did not deny the taxable loss (permanently, as s.54
  requires). The openings now apply to every book.
- **`check-filed` compares every taxable account, not only the locked
  ones.** An account added (or renamed) after `close-year` was never
  recomputed, so its dispositions were missing from the comparison and
  the check said OK. It is now recomputed in the same blend and reported
  as drift when it has activity in the filed year.
- **`phantoms.json` entries for an unknown account stop the run.** The
  file is keyed by account name, so renaming an account silently dropped
  its openings and changed the filed gain. `taxjson run` now names each
  entry whose account is not in `[accounts]` and suggests the closest
  current name.
- **Same-moment replacements: taxable first, then registered.** When a
  taxable rebuy and a TFSA/RRSP buy carried the same timestamp (common
  with Webull and Questrade stamps), a hash of the rows decided whether
  a superficial loss was deferred into the taxable ACB or lost for good,
  so a one-cent change on the TFSA row could move the taxable gain.
  At a tie the taxable acquisition now takes the denial first, then
  sheltered, then affiliated accounts (both engines; `taxjson
  tax-logic` lists the rule).
- **Moving shares between your own registered accounts no longer flips
  a superficial loss.** A custody move such as rrsp -> rrsp2 was netted
  out of the superficial-loss context, but the "still held at day 30"
  test runs per account: the receiving account looked short (a
  permanent denial for its in-window rebuy was missed) and the sending
  account looked long (a denial was invented). Netted moves now stay in
  the context as balance-only rows; they are never replacement
  property. The misleading "rrsp2 go short" hint goes away with it.
- **An IB/RBC-style assignment keeps its own premium when a Webull-style
  assignment on the same stock comes later.** The option premium of a
  plain-convention assignment (stock leg booked as an ordinary buy/sell)
  was held back for ANY later marked `ASSIGN` stock leg on that stock in
  the account, even a year later, and moved to that year. A marked leg
  now reserves only the premium of its own option leg (same account and
  underlying, within 7 days).
- **Decimal commas are refused, not read 100x too large.** `12,50` in a
  generic CSV, `-48,24` in a `.tt` line and `0,95` in a Webull cell had
  every comma stripped and were booked as 1250, -4824 and 95. A comma is
  now accepted only as a thousands separator (`1,234.56`); anything else
  stops the import with the file and line.
- **`.tt`: a negative sell total is refused.** `BUYSELL … -100 CAD 20
  -2000` was booked as -2,000 proceeds (a +1,000 gain became a -3,000
  loss). The total is the positive net proceeds; the error names the
  file and line.
- **Generic importer: strict mapping.** An unknown section or key
  (`ammount`, `commission`, `[format]`, `tax_sgn`) is refused with a
  suggestion instead of being ignored; dividend/tax/interest/fee actions
  require a mapped `amount`, and a blank amount on such a row is refused
  (it booked 0).
- **Generic importer: trade rows must add up.** A buy/sell with neither a
  price nor an amount (the fee became the whole cost), with no quantity,
  or a stock buy at zero cost is refused; so is a quantity/amount sign
  that contradicts the mapped action (a sell under an action mapped to
  `buy` was booked as a buy).
- **Generic importer keeps an explicit exchange suffix.** `DLR.U.TO`
  bought in USD became `DLR.U.US`, a different security, so a
  superficial loss across accounts was missed. A bare symbol still takes
  its suffix from the row currency.
- **Generic importer settles trades on the settlement date.** Every row
  was booked with the trade date as its settlement date, so a Dec-31 sale
  landed in the wrong tax year. A new optional `settle` column is used
  when mapped; otherwise buy/sell rows get the standard holiday-aware
  cycle (T+1/T+2/T+3 by era, options T+1) on the listing's market.
  `[options] settle_on_trade_date = true` keeps the trade date (crypto).
- **RBC: one identity across an account's yearly exports.** The parser
  learned a symbol's listing, an option code's contract and a temporary
  reorganization code's company from each file alone. Now all of an
  account's RBC files are read together. A US stock's USD dividend no
  longer lands on the TSX listing that shares its bare ticker (owner
  2024: HCA and NVDA dividends and withholding move from .TO to .US; no
  gain changes). A TSX stock's USD dividend or return of capital in a
  year with no trades keeps its .TO listing. An option re-described
  between exports (RCI vs RCI.B, XCH-adjusted TRP1) keeps one symbol, so
  its close is no longer booked as a new written option. A name change
  under a temporary code finds the old ticker in an earlier export, and
  warns with the `ticker.map` line when no file names it.
- **RBC: overlapping re-downloads are de-duplicated.** A row's time came
  from its position within the day, so the same trade in two downloads
  of one account got two ids and was booked twice. Rows already in an
  earlier file of the same RBC account are now skipped (identical fills
  on one day are matched by count), with a note; files without an
  Account column are never matched, and a warning says so.
- **RBC: ticker change without a reorganization row.** When one symbol
  stops with shares open and another with the same Symbol Description
  and currency opens with a sale they cover (ORCC to OBDC in 2023), the
  parser warns and prints the `GLOBAL` line for `ticker.map`.

- **The Canada estimate takes deductions.** `--deductions` (RRSP 20800,
  FHSA, RPP ...) and `--carrying-charges` (line 22100), or
  `deductions`/`carrying_charges` in `[estimate]` (which `instalments`
  reads too), lower net and taxable income; the AMT base takes the
  deductions in full and carrying charges at 50%. Before, a year with
  an RRSP deduction and little other income was overstated (the
  owner's filed 2025 mix: +16,082 before, +1,354 after) and a binding
  AMT could read as not binding.
- **A malformed `ticker.map` line stops the run.** A typo such as
  `TOBASE XYZ.US=XYZ.TO` or `TOBSE ...` dropped that rule, which changed
  ACB pools and the Schedule 3 gain, and the warning reached only
  `reports/*.sum` while `run` and `run --strict` exited 0. `taxjson run`
  now refuses the map, listing each bad line as `ticker.map:<line>`.
- **An export that parses to 0 transactions is loud, and fatal under
  `--strict`.** A Coinbase file with a renamed header, or a `kr_`-named
  file that is not a Kraken ledger, dropped its whole book with exit 0
  even under `run --strict`, and the checklist called the run clean.
  Every run (cached or not) now prints a WARNING naming the file on
  stderr, `run --strict` stops, and the checklist's `run-clean` step
  needs attention.
- **An Excel export in `inputs/<account>/` stops the run.** Only `.csv`
  and `.tt` files are read, so a Questrade `.xlsx` dropped in unconverted
  lost every trade in it with exit 0 and no mention. `taxjson run` now
  names each unconverted spreadsheet and stops (a spreadsheet next to
  its converted CSV only warns); an input folder with only a spreadsheet
  and no `[accounts.*]` section is named too; the checklist no longer
  counts `.xlsx`/`.txt` files as account activity and its `run-clean`
  step flags an unread spreadsheet.
- **Every command refuses an invalid account type, not only `run`.** An
  account `type` edited after the run (`"Taxable"`, or deleted) silently
  dropped that account from `estimate`, `instalments`, `sum`,
  `form-export`, `carryover` and `close-year` (which locked the wrong
  total), all with exit 0. Every command that reads `taxjson.toml`, and
  `taxjson serve`, now stops with the same message `run` gives.
- **A .tt file named after a broker no longer erases that broker's
  trades.** `questrade.tt` (or `webull.tt`, `ib.tt`, `generic.tt` ...)
  wrote its converted JSON over the broker's parse in `work/`, so every
  trade in that broker's CSVs vanished with exit 0. Converted .tt files
  now live at `work/<account>_tt_<stem>.json`; the first run after
  upgrading removes the old `<account>_<stem>.json` copies as stale.
- **Coinbase Advanced Trade on a crypto-quoted pair books both coins.**
  A fill on `ETH-BTC` booked only the ETH; the BTC spent (or received)
  was never disposed of (or acquired), so its gain went missing and a
  phantom BTC position stayed in the book. The quote coin's leg is now
  booked at the fill's stated value; Notes that don't say how much of it
  moved stop the parse.
- **Coinbase Buy/Sell with a blank Total.** It booked $0 cost or $0
  proceeds with no warning. The Total is now rebuilt from Subtotal ± fee
  (or quantity × price ± fee), with a note; a row with nothing to rebuild
  it from stops the parse, naming the row.
- **Kraken ledger trades missing from the trades export.** They were
  dropped behind the same note a complete run prints. Each unmatched
  trade is now an `UNBOOKED` warning on the console (count, dates,
  masked refids), and `taxjson run --strict` stops on it. A ledger whose
  trades are all matched no longer prints the "parsed to 0 transactions"
  warning.
- **Crypto price lookups that fail are no longer $0.** A Yahoo error, or
  a reply with no usable close, left staking income and cost basis at $0
  and the run said "Validation passed". Now fill-crypto warns, each
  unpriced row is a validation ERROR on the console, and `run --strict`
  stops. A crypto validation error also no longer crashes `taxjson run`
  with a TypeError.
- **`crypto_ticker.map` at the project root is always read.** It was
  ignored unless the cwd was the project root, and a map in the cwd
  applied to whatever project was run. `run --fast` now re-prices after
  the map is added, edited or deleted.

- **`taxjson spinoffs` and `taxjson splits`.** Every spin-off with its
  election, the value per share used and what was booked (income and the
  new shares' cost), flagging a taxable spin-off booked at $0 and showing
  the broker's own value when it reported one; every split,
  consolidation and rename with holdings before and after, flagging a
  split recorded twice, a no-op row and a fractional result.
- **`taxjson check-dates`.** Every trade and settlement date the parsers
  produced, checked against the calendar of what was traded: crypto 24/7,
  futures 23/5 (Sunday evening to Friday), US stocks on exchange days plus
  the overnight session, options and Canadian listings on exchange days;
  settlement never before the trade or on a weekend, and normally the
  standard cycle on either market's calendar. On the owner's books it
  found three option lots in the 2025 opening file dated Remembrance Day
  2024, a day neither market settles (the file carries trade dates).
- **One replacement backs one denial.** A 100-share rebuy (or one call)
  inside the window of a sale split into ten fills denied every fill's
  loss in full: ten times the loss it could back. Replacement units are
  now claimed in a fixed order, each by one denied unit, across fills
  and across losses; a call that expires before day 30 is not held on
  day 30. Owner books: 2025 -28.41, 2026 -86.53.
- **Year-to-year hand-off (`taxjson handoff`).** `close-year` now also
  records every sale, the positions and cost at Dec 31 (with the
  superficial-loss deferrals the full history decided), and the trades
  that settle in January; `--filed-dispositions` stores what a return
  prepared elsewhere actually reported. `taxjson handoff`, run in the
  next year's project (`[settings] prior_year_record`), checks the
  opening positions and cost, that every January settlement is booked
  here once, and that no sale is reported in both years; a cost
  difference is listed with its two consistent choices (keep the year as
  filed, or amend it). It is a `checklist` step and `edge-cases` points
  to it. Found on the owner's books: four RBC sales traded 2024-12-31
  that were reported in no year.
- **`taxjson tax-logic`.** A one-screen statement of every rule taxjson
  applies for the project's country (or `--country`), one line per rule
  (citing the Act where the rule comes from it), with the project's settings filled in
  where they change the answer. `--json` for machines.
- **`cross_asset` retired; puts are never replacement property.** A put
  is a right to sell, so the old warn-only "long put vs short-cover loss"
  scan is gone, and with the call rule enforced nothing is left opt-in.
  The rule is: an option (a long call) replaces shares, never the
  reverse; an option is replaced only by the identical contract. The
  `cross_asset` key and `--cross-asset` flag are accepted and ignored
  (`run` says so); the US engine prints its call warning without them.
- **Calls are replacement property for share losses (s.54 para (i)).** A
  long call on the same shares, opened within 30 days of a loss on long
  shares and still held on day 30 (any account, registered included), now
  denies the loss at 100 shares per contract; the denied amount is added
  to the call's cost (permanent when the call is in a registered
  account). It was a warn-only scan behind `cross_asset`. One-way by
  design: shares never replace an option, and only the identical contract
  replaces an option. `wash-radar`, `buy-check`, `sell-check` and
  `edge-cases` follow the same rule (buy-check and sell-check no longer
  let an option's row decide a share trade or another series' trade).
- **`taxjson edge-cases`.** One report of everything whose treatment turns
  on a boundary: trades that settle in the other year and where
  `tax_date` puts them, dispositions on the last and first days of a
  year, written options and expiries across Dec 31, income paid around
  New Year, crypto near midnight, loss windows spanning Dec 31 and
  deferrals carried into next year. For each taxable loss it lists every
  purchase (any account) or sale within `--margin` days of day 30, with
  the day count on both date bases, and flags where the basis alone
  decides. Long calls bought inside a share loss's window are listed as
  advisory (s.54 'right to acquire'; not enforced).
- **Settle dates skip holidays.** Brokers that don't print a settle date
  (IB, and Webull's trade date worked back from its settle date) were
  dated by skipping weekends only, so 250 of the owner's IB trades
  settled on a closed day (Good Friday, Labour Day, July 3 2026, Jan 1).
  That picked the wrong day's Bank of Canada rate and could move a
  repurchase into or out of the 30-day superficial-loss window. A new
  rule-based calendar (`lib/market_calendar.py`) counts settlement days:
  US = NYSE plus Federal Reserve holidays (Columbus and Veterans Day trade
  but don't settle); Canada = TSX plus Remembrance Day and the National
  Day for Truth and Reconciliation. It was checked against every settle
  date Questrade and RBC print. Equities before 2017-09-05 now settle T+3.
- **Futures settle on the trade date.** IB futures and futures options
  were given the stock T+1 settle date, so a close on Dec 31 landed in the
  next tax year. Variation margin settles their profit and loss daily, so
  the disposition is now the trade date. `[settings] futures_settle =
  "next_day"` restores the clearing house's premium date.

- **Coinbase staking income is the Subtotal.** Coinbase's Total adds back its
  staking commission ("Fees and/or Spread"), coins you never received; it
  was booked as income and as the rewarded coins' cost. Income and ACB now
  use the Subtotal (quantity x price).
- **Kraken: fees charged in the traded coin.** Kraken's trades CSV states
  every fee in quote units, even when Kraken took it in the coin you
  bought or sold, so the full `vol` was booked and phantom coins piled up
  (seen on real exports in six coins). With the ledgers export in the
  same folder,
  each fill is joined to its ledger rows (refid = trade txid) and booked
  from them: a coin fee reduces the coins received (buy) or adds to the
  coins given (sell), with no quote-currency fee. Any disagreement
  between the two exports raises. Without a ledger the parser warns that
  the fee currency can't be verified. Every crypto asset's parsed units
  now equal the ledger's amount − fee exactly. Crypto gains on real books
  move by tens of dollars a year (mostly this fix); staking income is
  unchanged.
- **Kraken: legacy ledger rows, suffixes, fee currency, required
  columns.** `staking` and `dividend` rows are income (they were
  dropped); `.S`/`.M`/`.F`/`.B`/`.P`/`.HOLD` asset suffixes fold to the
  bare coin, and `ETH2` to ETH; legacy spot-to-staking transfers are
  wallet moves; `feecurrency` is honoured (a reward whose fee is charged
  in another currency is credited in full, the fee reducing the income);
  a coin-denominated withdrawal fee is booked as a disposition of the fee
  coins; a missing `fee`/`vol`/`amount` column, or an unparseable number,
  raises instead of reading as 0.
- **Kraken: multi-asset dust sweeps book every swept coin.** One refid
  spending several assets for one receipt kept only the last spend leg
  (a warning, then the others stayed in the book forever). The receipt is
  now split across the legs by `amountusd` (equally when absent).
- **Coinbase: USDC is US-dollar cash, as on Kraken.** `Buy USDC` rows
  are stablecoin conversions (not positions), and the USDC leg of an
  Advanced Trade on a `*-USDC` pair is cash. Before, the USDC bought was a
  position that the Advanced-Trade spends never reduced (a phantom USDC
  long). Strictly a stablecoin is a crypto-asset for the CRA; what the
  cash model leaves out is the USD/CAD movement while it is held — a few
  dollars a year on real data.
- **Kraken and Coinbase rows are dated in local time.** Both exchanges
  stamp UTC; rows are now converted to America/Toronto (override with
  `TAXJSON_LOCAL_TZ`), so a trade at 03:00 UTC on January 1 lands in
  the previous tax year. None of the real 2025/2026 rows straddle a year
  end; the shift moves some dates by a day (FX/price day). Coinbase and
  Kraken amounts like `CA$4.00`, `US$-3` and `(12.00)` now parse;
  anything unparseable raises (`CA$4.00` used to read as 0).
- **Generic importer refuses mis-mapped columns.** Two fields on one
  header, |amount| ≠ qty × price (× 100 for options) ± fee beyond 1%, and
  a fee above 5% of the gross (unless `[options] allow_large_fees =
  true`) now stop the import; an amount equal to qty × price while the
  fee is nonzero warns that the GROSS column is probably mapped as
  `amount`. The currency must be mapped or set in `[defaults]` — there is
  no implicit USD any more (the shipped example already sets it).
- **`.tt` files: unknown actions are errors.** A line starting with
  `SELL`, `buysell`, `BUYSEL` or `Dividend` was skipped silently with
  exit 0; it now fails with the valid actions and a did-you-mean. Dates
  must be YYYY-MM-DD and times HH:MM:SS; inline `# …` comments are
  stripped (a `# DECLARED` remark no longer grants attestation); stray
  trailing tokens are errors; warnings and errors name `file.tt:line`.
  All real `.tt` files convert byte-identically.
- **RBC: every reorganization booked as one event.** RBC books name
  changes (NAC), reverse splits (REV), 1-for-1 exchanges and blank "MGR -"
  splits (MGR), MER reorganizations and option adjustments (XCH) as a
  removal under a temporary code plus a receipt. Only "MERGER TO" pairs
  were handled; the rest became $0 buys and sells on RBC's internal codes,
  and "SHRS RECEIVED THRU MERGER" receipts vanished. Each removal is now
  paired with its receipt (same company, within 7 days) and booked as ONE
  SPLIT (renaming when the ticker changed; a 1-for-1 on the same ticker
  books nothing), with MER's "ROC OF C$x" as a return-of-capital ADJUST
  and cash in lieu as the sale of the fractional share. An option XCH
  keeps the same contract (and its ACB). "REVERSE ENTRY" corrections
  cancel their leg. Unmatched legs, and any emitted symbol that is an RBC
  internal code, are warned about (a lint failure). Real mergers still go
  to the election prompt; spin-offs ("DIS - ... SPINOFF") now do too
  (s.86.1 or an FMV dividend in kind) instead of becoming $0 buys; rights
  issued to all shareholders stay a nil-cost acquisition, now noted.
- **RBC: income by code, never by security name.** Rows are classified
  by Activity and the RBC code ("DIV - ", "CASH DIV ON", "DIST ON"); the
  word DIVIDEND inside a name ("DIVIDEND 15 SPLIT CORP", "HIGH DIVIDEND
  ETF") turned in-kind transfers into $0 dividends and dropped their
  shares. An income row that carries shares is refused. Reinvestments
  (REI) are purchases of the units, not negative dividends; "ADJUSTMENT
  TO BOOK COST $x" rows are signed ACB adjustments (notional distribution
  up, return of capital down); ADR fees (FCH) are FEE rows; in-kind
  transfers carry RBC's "BOOK VALUE" as evidence (`book_value`, shown by
  `taxjson transfers`), booked exactly as before.
- **RBC: strict reading.** The header row must carry the real columns
  (a preamble line mentioning "Date" no longer passes); numbers must
  parse ("39 043" and garbage raise, "(12.50)" is negative); each date
  column gets ONE format for the whole file and a file that reads as
  both day/month and month/day raises; currencies are upper-cased; an
  unquoted comma is re-joined only inside the last (Description) column.
  Options described only in "Symbol Description" are recognised, one RBC
  option code is one contract (the first description wins, with a
  warning), known cash rows and footers count as non-events, and an
  unclassified row that moves shares or cash is a loud warning and fails
  `--lint`. Same-day rows keep the export's order (it lists newest
  first; an option assignment's two rows share one time so the premium
  still folds into the stock leg), and the older "HORIZONS U S DLR" line maps to DLR.U.TO like
  "GLOBAL X US DLR".
- **Duplicate split rows warned.** When one account carries the same
  split twice (a parser that now books it plus a manual .tt SPLIT line),
  merge2 warns "duplicate split" (it is applied once); the same event
  with different ratios warns "conflicting splits".
- **IB: Transaction Fees are no longer charged twice.** IB's Trades
  Comm/Fee already includes per-fill levies (UK stamp tax, SEC/FINRA
  fees); the Transaction Fees section only breaks them down (the Cash
  Report shows Commissions + Transaction Fees = the Comm/Fee sum). The
  parser folded the levy into the trade again: on a real 2025 AWE buy the
  fee drops from 103.74 to 54.34 GBP and the cost basis from 9,983.74 to
  9,934.34 (IB's own Basis). A layout that ever excluded the levy now
  fails the Cash Report check instead of under-booking.
- **IB: commission rebates keep their sign.** A positive Comm/Fee is a
  rebate; abs() booked it as a charge, overstating costs and understating
  proceeds by twice the rebate (real 2025 margin statement: 65 rows,
  245.95 USD; 2026: 195 rows, 624.94 USD; RRSP 17.66 and 6.04 USD). The
  fee is now negative for a rebate and net_amount is the cash IB moved.
  Questrade's commission is signed the same way (none of its real
  exports carries a rebate).
- **IB and Questrade: missing columns fail the parse.** Every Trades,
  Dividends, Withholding Tax, Interest, Fees, Corporate Actions, Transfers
  and Commission Adjustments column that carries money, quantity, price,
  date or currency (IB), and all 14 Questrade columns, are resolved by
  header name and required; a renamed or missing one used to read as 0
  (every fee or every gross silently gone) or as USD. Money cells parse
  strictly (a decimal comma, text or a blank is an error; parentheses
  and the unicode minus are negative), dates must be ISO, and each trade
  row must add up: |proceeds| = |qty| x price x multiplier (IB's own
  instrument multiplier — CL 1000, ES 50, MET 0.1, SI 5000; a futures
  contract size is never guessed) with a plausible commission, and for
  Questrade Net = Gross + Commission.
- **IB: parsed money reconciles to the Cash Report.** Per currency,
  Dividends, Payment in Lieu, Withholding Tax, Broker Interest, Other
  Fees, Commissions (+ Transaction Fees) and Trades (Sales + Purchase)
  must match IB's own Cash Report within 0.02, or the parse fails naming
  the currency and line. All 17 real Activity Statements reconcile.
- **IB: unknown sections are loud.** Only known metadata sections are
  quiet; an unknown section with money columns (a localized
  "Dividendes", a renamed "Transactions") fails the parse, any other
  warns. A "Realized Summary" report is refused instead of being read
  as activity.
- **Schema: declared multipliers make the notional check an error, and
  `taxjson run` parses with `--strict`.** IB and Questrade trade rows
  carry their contract multiplier; a net amount far from qty x price x
  multiplier is now a schema error for such rows (the 1/100 guess made
  every futures row a false positive — 61 warnings on the real 2025/2026
  margin statements, now 0). Every real 2025/2026 input parses with zero
  schema errors, so `taxjson run` now passes `--strict`: schema errors
  stop the run instead of scrolling past.
- **IB: a cancelled corporate action is undone.** A `Ca` row now removes
  or adjusts its original split leg, cash in lieu, tender sale, spinoff
  or untranslated merger; a restated split 3:1 -> 2:1 leaves one SPLIT
  2.0 (both were booked). A cancellation whose original is not in the
  statement is a loud skip.
- **IB: option root renames share one symbol.** When IB relists an
  adjusted option under a new root after a corporate action (DFDV ->
  DFDV1, one contract id in the instrument list), both legs are booked
  under the original root, so the assignment folds the premium; a
  ticker.map DFDV1 -> DFDV rule becomes a no-op. Monthly futures options
  use their real expiry from the instrument list instead of day 20 of
  the delivery month.
- **IB: smaller fixes.** `ADR;Po` / `ADR;Re` dividend accruals pair by
  token; Warrant and futures-option transfers are booked (they were
  treated as cash); a consolidated statement spanning several accounts
  warns (ids masked); an income row whose ISIN country has no suffix
  mapping warns when no position confirms the assumed .US listing.
- **Questrade: no first-match symbol rebinding.** Only internal codes
  (S098765) and dotted dividend codes (.BTO -> the traded BTG) are
  rebound to the traded symbol; a real ticker is kept, and a
  description matching several traded symbols is never guessed (older
  RESP exports booked FTN.PR income under FTN.PRA; it now stays FTN.PR
  with a warning to add a ticker.map rule).
- **Questrade: quantity-bearing DIS rows are corporate-action legs.**
  Warrant spinoff / rights legs (+100 / -100 / +100, no cash) were
  counted as "zero-net dividend (informational)"; they are now their own
  category and reported (taxjson-corp-actions books them); any other
  quantity DIS row is a loud skip.
- **Questrade: taxable-account caveats warn.** A dividend marked NON-RES
  TAX WITHHELD is booked at the net (the export has no gross or tax), and
  a transfer-in without TRANSFER BOOK VALUE enters at $0 cost; both now
  warn in taxable accounts (`taxjson-brokerage --account-type`, passed by
  `taxjson run`).
- **Questrade .xlsx exports.** `taxjson-xlsx-to-csv` reads every cell as
  text (dates arrive as ISO text, the ticker NA stays NA, a decimal comma
  is not turned into thousands), and the parser accepts ISO dates
  ("2025-12-31 00:00:00"); a settlement date that is present but
  unparseable is an error instead of a silent T+1 fallback.
- **Parsers: parentheses are negative.** `clean_number` read "(1,352.97)"
  as +1352.97; it is now -1352.97 and it warns on unparseable text.
  Webull (the one real user of parentheses) takes the magnitude itself,
  so its outputs are unchanged.

- **Webull: columns by header label, both export layouts pinned.** The
  Trading Summary's 2024 layout has 9 columns (Proceeds in column 8), the
  2025 layout 10 (an empty column 8). The parser read by position with a
  column-9-then-8 fallback; it now resolves every column from the header
  labels, refuses an unrecognised layout instead of guessing, and fails
  on any row-accounting mismatch. Tests pin the amounts of both layouts.
  (A different, older flow read the 2024 layout by position and booked
  15 Webull purchases at $0 cost on a filed return.)
- **Webull: option assignment and exercise detected.** The Trading
  Summary shows an assignment or exercise only as a $0 option close plus
  a stock trade at the strike. Both legs are now marked ASSIGN when the
  stock trade matches in quantity, direction and price within a few
  days, so the premium folds into the shares' cost (s.49(3)) instead of
  being realized as an expiry; seen on real 2025 exports (LULU and DOCU
  put assignments, a DELL call exercise).
- **RBC: split-corp retractions are dispositions.** RBC books an issuer
  retraction as an `Other` row coded `TEN` ("... RETRACTION AT C$x PER
  SHARE") with a blank price; it was skipped as unclassified, so the
  shares never left the books and the proceeds and gain were missing
  (seen on real 2023/2024 exports). Filed numbers change for any year
  with a retraction.
- **No security identity by suffix stripping.** `buy-check`, `sell-check`,
  `harvest` and `scan` matched listings by root, so `XYZ.TO` and `XYZ.US`
  were one security unless ticker.map said `DISTINCT` (it merged Digital
  Realty DLR.US with the Global X DLR.TO currency ETF on real books). Two
  listings are now the same security ONLY through a ticker.map rule
  (GLOBAL/TOBASE/JOURNAL), a split rename, or an option's own underlying,
  exactly as the engine pools them. A bare query (`buy-check XYZ`) still
  finds every listing of that ticker, each with its own verdict.
  `DISTINCT` now only records a settled pair for the scan's MAP-GAP check.
- **Estimate: Ontario AMT corrected** — the Ontario additional tax for
  minimum tax is 24.63% of the federal AMT excess from 2024 (it was a
  flat 33.67%, the pre-2024 factor), and the Ontario surtax is now
  recomputed on basic ON tax plus that amount (5006-D "Line 72"). The
  2026 factor is marked assumed until the 2026 form is published.
  Gains-only $400k, ON 2026: provincial AMT 1,216.75 → 1,388.50.
- **Estimate: BC AMT factor by year** — 33.7% (2024), 34.9% (2025),
  40.0% (2026), per BC Income Tax Act s.4.8 (was 33.7% for all years).
- **Estimate: Alberta 2026 eligible dividend credit** is 8.12% (the
  table had 8%); **BC 2026 BPA** 13,216 (was 13,217).
- **Estimate: federal BPA phase-down** — the enhanced basic personal
  amount now falls linearly on net income between the 29% and 33%
  bracket thresholds (2026: 16,452 → 14,829), in regular tax and in
  the AMT's 50% credit. ON, 250k other income + 300k gains, 2026:
  estimate 79,955.12 → 79,980.14.
- **Estimate: Ontario Health Premium** (up to $900) is modelled for ON
  and shown in the `--verbose` trace.
- **Estimate: crypto staking** rewards (a crypto account's dividends)
  are ordinary income with no withholding — they were counted as
  foreign dividends with an assumed 15% foreign tax credit.
- **Estimate notes**: the block now says which surtax/health premium
  applied, the phased BPA, the provincial AMT arithmetic, and — when
  the project year has no built-in rate table — which year's tables
  ran (a year before 2024 also says the post-2024 AMT did not apply).
  The assumptions line keeps disclosing that non-eligible dividends are
  treated as eligible.
- **Instalments**: credit interest runs from the later of the payment
  date and January 1; net interest of $25 or less is not charged; the
  report says CRA charges instalment interest only after a reminder.
  With no `prescribed_rate(s)` set, CRA's published quarterly rates
  (2024–2026, built in) are used instead of 0%. Scaffold and README
  rate examples corrected (2025 Q3 onward: 7%).
- **`close-year` no longer locks the wrong year**: after bumping
  `[settings].year` without a rebuild, it wrote LAST year's books into
  `filed/<new year>.json`. It now compares the year recorded in each
  work/ gains file with `[settings].year` and refuses on a mismatch
  ("rebuild with `taxjson run` first"); `sum`, `estimate` and
  `form-export` print a loud WARNING instead.
- **An account without `type` is now fatal** (it silently defaulted to
  sheltered, so an untyped taxable account dropped out of the return);
  the message lists `taxable | sheltered`.
- **No more false "run `taxjson run` first"** after a successful run:
  `run` records accounts it skipped for having no inputs
  (`work/skipped_accounts.json`), and t1135, form-export,
  reconcile-slips, harvest, audit, `list --date`, wash-radar and
  buy/sell-check stay quiet about them (helper
  `_accounts_skipped_for_no_inputs`).
- `inputs/slips/` (the T5008/1099-B CSVs the checklist asks for) no
  longer triggers the "has no [accounts.slips] section" warning; CSVs
  in a SUBfolder of an account's inputs now warn that they are not read.
- `run` on a project where no account has any input says so; with
  `--strict` it exits 1 instead of "Done". A typo'd settings key's
  did-you-mean now prints before the "missing year" error it causes.
  `-C DIR` naming a missing directory says so; `sum` in a non-project
  says there is no taxjson.toml.
- `run`'s holdings check tells a config problem (missing holdings file)
  apart from real position differences.
- `init`: the next-steps line is `taxjson -C '<dir>' run` (the old
  `taxjson run -C <dir>` failed); `--year` is capped at next year;
  `--force` keeps the previous config as `taxjson.toml.bak` and lists
  input folders the new config no longer covers.
- `serve`: malformed taxjson.toml is reported like every other command
  (no traceback); `--port` must be 1-65535. Web UI: error pages for an
  unknown account return 404; the what-if rejects a negative quantity
  (it was simulated as positive).
- `elect`: `--pending --json` emits JSON when nothing is pending;
  `elect ACCOUNT --json` lists that account's elections as JSON (and
  `--json` with `--set/--redo/--reset` is refused); `--hint` without
  `--set` is an error; `--set` with an event id that matches nothing is
  refused instead of saving a junk record into manifest.json.
- `audit`: `--date` matches the trade OR settlement date (and says
  which); `--summary` shows ids long enough to be unique; an unknown
  `--account`, or a symbol/`--id`/`--date` filter that matches nothing,
  exits 1 with a message instead of an empty "0/0 ✓" block;
  `--account` runs only the computation holding that account.
- `find-missing-history --gen-phantoms` passes the project country
  (no more "--country not given; assuming canada" per account).
- `form-export`: `--out`/`--box` without `--form txf` are refused
  instead of silently ignored.
- `estimate`/`sum --estimate`: a missing or unsupported province fails
  BEFORE the table prints, and errors name the invoking command;
  `sum --province` without the estimate warns that it is ignored.
- `scan` before any run exits 1 instead of reporting a clean scan;
  `leaps`/`leaps-sum` with no books say so and exit 1.
- Help/docs: `fetch --positions` no longer cites the removed
  `taxjson verify`; the module docstring no longer lists `taxjson
  show`; `taxjson-fees-sum` usage examples; futures filter help;
  `reconcile-slips --json`, `serve --port` and `harvest --ibkr-port`
  document themselves/their defaults. README: `[estimate]` vs
  `[instalments]` keys split into their own blocks, the chaining row's
  duplicate example and check-filed's nonexistent `--strict` fixed,
  fetch windows described as the code does them, `inputs/slips/` in
  the file table, the estimate sample shows the real FTC label.
- **IB: option expiries settle on the expiry date.** Rows coded `Ep`,
  rows of the Options Expirations section and zero-price closes of an
  option were given T+1 like every trade, so a Dec-31 expiry landed in
  the NEXT tax year on the settle basis; now `date_settle = date` (also
  futures final settlement). A same-contract trade executed on the
  expiry day (0DTE) has its T+1 settle clamped to the expiry so the
  expiry closes the position it opened. Assignment/exercise option legs
  keep T+1 (they must share the stock leg's settle date).
- **Questrade, RBC: expired options are booked on the contract's expiry
  date**, settle = date. Both brokers post the expiry on the next
  business day (Friday expiry dated Monday), which moved a Dec-31 expiry
  into the next year; a blank settle column also added T+1 on top. RBC's
  blank-settle fallback no longer returns the raw "January 5, 2028" cell
  as `date_settle` (it now computes an ISO T+1 / era-aware settle).
- **Webull: expiry rows are no longer shifted a business day earlier.**
  The Date column is the settlement date for trades but the EXPIRY date
  for zero-price expiry rows; walking it back put a 0DTE long's expiry
  before its buy, booking a phantom $0 short WRITE. The expiry now
  closes the long (direction LONG, loss = premium); gain totals are
  unchanged.
- **Kraken: staking rewards book the coins actually credited** — amount
  − fee, where the ledger fee is in the reward's own coin units (the
  balance moves by amount − fee in both the pre-2026 and 2026 formats).
  Income and acquired quantity drop by Kraken's commission (typically
  20–30% of the gross reward), the phantom units are gone, and the coin
  fee no longer lands in the USD fee field. Instant trades and
  crypto/crypto trades-CSV fills fold their coin-unit fees into the
  crypto leg's quantity the same way.
- **Kraken: `hybridearnwithdrawal` rows are custody evidence**, like a
  withdrawal (kept aside, never in the book). They were non-events on
  the assumption of an internal Earn move, but the rows carry a funding
  refid, have no counter-leg in any earn wallet and sweep the spot
  balance to dust — the coins left the ledger.
- **IB: "(Return of Capital)" is no longer an ACB reduction when it
  can't be one.** A payment in lieu labeled ROC is income from the share
  borrower (`DIVIDEND_IN_LIEU`); ROC from a non-Canadian issuer (ISIN
  country ≠ CA) is a foreign dividend under ITA s.90(2), noted in the
  description. Canadian-issuer ROC stays an ACB reduction. New
  `[settings] foreign_return_of_capital = "dividend" | "acb"` (default
  `dividend`; `taxjson-brokerage --foreign-roc`) restores the ACB
  treatment for foreign issuers. Income rises, later gains fall, for
  affected holdings.
- **.tt validation: option lines are checked against qty × price × 100**
  — every option row used to warn "differs from qty*price".
- **IB: no false "accrued but not booked" warning when IB revises a
  dividend's pay date** between the accrual and its reversal (Po/Re now
  net per ex-date; a posting within a week in any currency matches).
- **Futures shorts are not "truncated history" candidates** — an IB
  futures sell-to-open is excluded from the go-short hint like an option
  write (`--include-options` still shows them).
- **FX rates now come from the Bank of Canada.** For a CAD base,
  `taxjson run` converts at the Bank of Canada daily average rate
  (Valet API, series `FX<CUR>CAD`), the rate CRA expects (Folio
  S5-F4-C1) and the one the docs always claimed — earlier releases
  actually used Yahoo Finance closes. Yahoo is now only the fallback
  for dates before 2017-01-03 (where the Valet series begin) and for
  currencies the Bank does not publish. **Computed CAD amounts will
  move by cents to dollars per trade versus earlier releases**: over
  2021–2026 the Yahoo USD/CAD close differed from the Bank's rate by
  0.17% on the median day (about $17 per US$10,000), 0.6% at the 95th
  percentile and up to 1.7% on volatile days (GBP and AUD somewhat
  more). Each rate
  in `work/to_base.csv` carries its source in a new sixth column
  (`boc` / `yahoo`; loaders still read the first five), the run
  prints `FX USD→CAD: Bank of Canada Valet for N dates, Yahoo
  fallback for M`, and each account's `.sum` DIAGNOSTICS gets the
  same count for the rates its rows actually used. Weekends and
  holidays take the most recent prior business-day rate, as before.
  Non-CAD (US) projects still use Yahoo Finance.
- **FX history no longer ends 5.5 years back.** Rates were fetched for
  "today minus 2000 days", so any older transaction silently converted
  at the 1.35 default rate (stderr only; the run exited 0 and the
  checklist said clean). Rates now reach back to 2000-01-01
  (`taxjson-to-base-curr --start`), and a row converted at the default
  rate is a **validation ERROR** — counted in the `.sum` DIAGNOSTICS
  `validation: N error(s)` line and `taxjson checklist`, fatal under
  `run --strict` (crypto books too). An explicit `--default-rate`
  still accepts the fallback.
- **`TAXJSON_OFFLINE`**: the FX stage no longer demands a rate for
  TODAY (a cache warm from yesterday failed offline every new day,
  even for a past-year or all-CAD project). Offline it serves the
  cache and never fails; only dates a transaction actually needs
  matter, and a missing one is a validation error. `TAXJSON_OFFLINE=0`
  (or `false`/`no`/`off`) now means off — it used to switch offline
  mode ON; only `1`/`true`/`yes`/`on` turn it on.
- `taxjson-to-base-curr` no longer imports pandas at start-up: a
  core-only install (install.sh's silent fallback) crashed the first
  run with `ModuleNotFoundError`. The Bank of Canada path needs no
  extra; the Yahoo fallback asks for `taxjson[fx]` by name, and
  install.sh now warns loudly when it falls back to the core package.
- **`taxjson fx-cash`**: withheld tax left the foreign-cash pool twice
  (the dividend was booked net of withholding AND its TAX row was
  spent) — RBC's implied-tax rows and every IB dividend paired by
  merge2. Dividends and interest now enter the pool GROSS; the TAX row
  takes the withholding out once. Phantom overdrafts on withheld
  dividends disappear and the gain changes accordingly.
- **`taxjson t1135`**: a split held in two taxable accounts was applied
  once per account (the pool doubled — cost at Dec 31 overstated);
  the walk now dedupes split events like the gains engines. Crypto
  (suffix-less symbols) is reported under a `CRYPTO` country bucket
  with a "check where held" note instead of the unclassified `??` —
  crypto on a foreign exchange is generally specified foreign
  property; map it in `t1135.map`.
- **`taxjson checklist`**: detectors no longer say "done" on nothing —
  missing-history with no base files and option-boundary with no taxable
  book are blocked; option-boundary reads `--json` and reports ATTENTION
  rows and an unset `option_grant_timing_since`; form-export now compares
  its totals with `sum`'s FOR THE RETURN block and the taxable accounts'
  realized gain (it compared nothing); a T5008 finding names the actual
  mismatch counts and symbols instead of the report's last note line. A
  step marked `--done` whose detector says attention now shows `[!]` with
  the mark and note beside the finding and keeps the list open (it
  counted as done). `--undo` with no mark and `--reset` with no file say
  so; `--note` without `--done`/`--skip` is an error; mark commands honour
  `--json`; `--only` with an unknown id lists the ids; `--walk` re-prompts
  on an unknown key with the key list and exits 1 (after the summary)
  when quit or left with open steps. US projects get the US step names
  (1099-B, Form 8949 / Schedule D, 1099-DIV, Form 1116, §988) and `n/a`
  for option-boundary, T1135 and the NOA; a project with no taxable
  account gets `n/a` rather than blocked for the taxable-only steps.

- **Crypto accounts blend across exchanges** (Canada): with two or more
  taxable `crypto = true` accounts, `taxjson run` now runs ONE blended
  crypto pass (like the equity blend) producing each account's
  `<account>_gains_wash.json` — ITA s.47 averaging and the superficial-
  loss rule reach identical crypto held on different exchanges. Each
  exchange's book was computed alone, while the run printed a note
  claiming the blended pass covered them (a sale at a blended-ACB loss
  replaced on the other exchange filed -10,000 allowed instead of 0).
  `audit` and `check-filed` recompute the same way; a single crypto
  account and US crypto (no §1091) are unchanged, and the overlap note
  now only names accounts a blend actually spans.

- **`taxjson carryover` ignored the option-timing settings**: the ledger
  ran every year on close timing while the returns were filed on grant
  timing (a filed 2025 of -601 showed as -1,000). The wrapper now passes
  `option_premium_timing` / `option_grant_timing_since` /
  `option_buyback_loss_superficial` (new `--option-*` flags on
  `taxjson-carryover`), as do the `list --date` as-of recompute and the
  raw base-currency holdings pass.
- **`option_grant_timing_since` no longer drifts with `year`**: unset, it
  defaults to the project year, so consecutive default projects taxed a
  year-straddling premium twice (+399 in 2025, +298 in 2026 for a 298
  economic gain). `taxjson init` now writes the key uncommented; `taxjson
  run` and `option-boundary` warn on every run while a Canadian grant-
  timing project leaves it unset; `close-year` records the timing in
  `filed/<year>.json`; and `option-boundary` flags ATTENTION on a
  contract written in a locked year but kept on transition close timing
  here (definite when the lock records grant timing).
- **`taxjson option-boundary`**: a contract past its expiry date within
  the tax year with no expiry/assignment row is flagged "expired but no
  expiry/assignment row — check the export" instead of listed as open;
  with no taxable book in work/ the command exits 1 ("NOT CHECKED")
  instead of printing the all-clear; `--json` adds `attention`, `amend`,
  `since_explicit` and `missing_books`.

- **Schedule 3 line routing** (`form-export --form schedule3`, `taxjson
  sum`): every disposition went on line 13199/13200 ("section 3, publicly
  traded shares"). Rows are now routed by property type to the 2025 form's
  Part 3 lines — line 4 shares and fund units (13199/13200), line 6
  options, futures and other properties (15199/15300; T4037 lists options
  there), line 7 crypto-assets from `crypto = true` accounts (15200/15301;
  15199/15300 for 2024 and earlier) — with per-line totals in the text,
  CSV (`line`, `proceeds_line`, `gain_line`, `property`, `denied` columns)
  and JSON (`lines`, per-code `totals`). Standalone `taxjson-form-export`
  takes crypto books with `--crypto FILE`.
- **FOR THE RETURN block**: one row per Schedule 3 line (was per
  account; the per-account split stays in `--json`), each with its line
  number and codes. The footer said COST *includes* the denied
  superficial losses — backwards: the ACB shown is REDUCED by the denial
  so proceeds − ACB − outlays is the allowed gain, and the denied amount
  goes onto the replacement's ACB. form-export's per-row ACB now uses the
  same convention, so every row foots (a fully denied row used to show
  proceeds 1,955.52 − ACB 11,536.54 − outlays 1.40 against a gain of
  0.00). US projects show Form 8949's own Part I/II (d) proceeds, (e)
  cost, (g) adjustment and (h) gain — the block used to show Schedule 3
  style proceeds and a cost net of the wash adjustment that matched
  neither 8949 column. A line under the block gives the `fx-cash`
  s.39(1.1) estimate (line 15300), or a pointer when it cannot be built.
- **Security/privacy audit (2026-09).**
  - `taxjson redact` also strips names in free text (`Initiated by`,
    `Payee:`, honorifics, `wire from`, the RBC `Account: …,` holder,
    Webull preamble name/street/city lines), account numbers after
    `acct` / `a/c` / `transfer from|to` and alphanumeric ids, IB Flex
    `AcctAlias`, phone numbers, Canadian postal codes, street
    addresses, SIN/SSN-shaped numbers, and crypto wallet addresses and
    exchange transaction ids (stable same-shape pseudonyms). It refuses
    `.xlsx`/binary input (it used to write a corrupt, unredacted copy),
    fails closed on an invalid `--also`/denylist pattern (exit 2,
    nothing written), `--check` exits 1 when it finds something, and
    the report lists (by line number) free text still worth reading.
    It strips what it recognises — review the output before sharing.
  - Owner-only files: `taxjson` and every `taxjson-*` tool add 077 to
    the umask, so `work/`, `reports/`, `filed/`, `export/`,
    `checklist.json` and a new project's `inputs/<account>/` are
    0600/0700. `fetch` writes statements 0600 and tightens an existing
    `inputs/<account>/` to 0700. Existing projects: `chmod -R go-rwx`.
  - `taxjson serve --host <non-loopback>` requires a random per-run
    token (printed URL `?token=…`, then an HttpOnly cookie); `/healthz`
    no longer returns the project path.
  - `fetch`: the Questrade token is only sent to
    `https://*.questrade.com`; `~/.questrade_token.part` is created
    fresh (O_EXCL, never through a symlink); the live-holdings TOML
    escapes broker-supplied strings.
  - `python-multipart >= 0.0.18` (CVE-2024-53981); Dependabot for pip
    and GitHub Actions; the dev repo ignores run outputs (`work/`,
    `filed/`, `export/`, `checklist.json`, `*.xlsx`, `*.pdf`).
  - `scripts/check-pii.sh` / pre-push: scans the identities of new
    commits and annotated tags (your own configured identity only
    warns), tag messages, the new path of a pure rename; denylist
    matching is case-insensitive and separator-tolerant and never
    echoes the private string; 8-digit file-name tokens are exempt only
    when they are real dates; `pii-ok` must be a comment marker
    (`# pii-ok` / `pii-ok:`).
  - SECURITY.md: keep a tax project repository private; FX comes from
    the Bank of Canada Valet with Yahoo Finance as a fallback; what
    Yahoo lookups reveal (held tickers and dates).
  - Test fixtures copied from real statements replaced with synthetic
    values.

- **Return of capital after the position is sold (Canada).** A ROC that
  posts when the pool is empty has no ACB to reduce; it is now a capital
  gain in the year received (s.40(3) with a nil ACB, noted on the row)
  instead of silently lowering the NEXT purchase's ACB (which moved the
  gain to a later year). Positive ADJUSTs on an empty pool are unchanged.
- **Mergers with per-account delivered ratios (Canada).** A merger whose
  accounts received different whole-share counts (15 → 15 in one, 40 → 41
  in another) emitted one rename per account at its own ratio; the
  symbol-wide pool was renamed at the first account's ratio and the
  second found no pool (1,100 booked for 1,220, a phantom short share).
  The rows now fold into one event at the holdings-weighted ratio, so
  the pool lands on the shares actually delivered.
- **Superficial loss (s.54): only substituted property backs a denial.**
  A denial now needs units acquired inside the 61-day window that the
  same holder (the taxable pool, or one registered / affiliated account)
  still owns at its end. Units a registered account held before the
  window no longer back one: a taxable rebuy sold again inside the
  window, with an RRSP holding shares bought years earlier, was denied
  PERMANENTLY (-1,000 → now allowed); a partly-sold taxable rebuy now
  defers only what is still held instead of turning the rest permanent.
  (A short taxable balance at day 30 also no longer offsets a registered
  account's still-held acquisition.)
- **Superficial loss: a trigger's side of the loss follows the pool's
  own ordering.** A rebuy that settles on the loss sale's settlement
  date but traded the day before (a later clock time) is in the pool
  the sale draws from; it was treated as bought after the loss, its
  basis bump landed before the sale and inflated the loss it deferred
  (-1,000 / 250 denied instead of -800 / 200), and some short-cover
  books never converged. Pre/post-loss is now decided by the phase
  ladder the pool replays with.
- **Negative proceeds (Canada).** A sell whose commission exceeds its
  gross — closing a worthless option at $0.01, writing one for less
  than the fee — now books its proceeds signed (negative) instead of
  as a positive amount: the loss grows by twice the shortfall (-201.00
  was booked for a -220.90 loss). Buys are unchanged.
- **s.49 grant timing: an assignment folds each premium once.** Every
  short opening — a write before `option_grant_since`, the short
  leftover of a sell that crosses zero — is now a lot, consumed FIFO by
  write date. An ASSIGN that consumed a lot with no grant record used to
  fold the premium into the shares on top of the grant already
  recognised (a book that totals 450 under close timing totalled 650
  under grant timing with `since = 2025`); totals are now identical
  under both settings (fuzzed with assignments and every `since`).
  A close-timing (pre-`since`) lot now closes before a later grant lot
  and at its own premium, and a buy-back of a grant lot is a loss of
  exactly the amount paid even when other lots were written at other
  premiums — both move amounts between years, never the total.
- **check-pii closes five gaps.** Ad-hoc mode matches file names
  relative to the scanned argument (a home directory named after its
  owner no longer fails every scan) and hides each matching path
  component; every file that is not a known binary type is scanned as
  text and fails closed on NUL bytes whatever its extension (a UTF-16
  `.tsv` or `.log`); an exempt synthetic token no longer hides a real
  id, account number or e-mail on the same line or in the file path; a
  configured `TAXJSON_PII_DENYLIST` that is missing, or a denylist that
  cannot be read, fails instead of passing on the generic patterns; and
  a spaced or dashed 3-3-3 number with a valid SIN check digit is a hit.
- **T1135 cost includes denied superficial losses.** The cost walk now
  adds the amount the engine denies under s.54 to the replacement
  property's cost (s.53(1)(f)), as the ACB does, so the year-end and
  maximum cost columns and the $100,000 threshold test no longer
  understate after a superficial loss in the project year.
- **TOBASE no longer pools a US option into a Montreal contract.** When
  a share rule's root rename would move a US-listed option onto a
  contract code the account also trades on the Montreal Exchange, the
  US contract keeps its own symbol (different strike currency and
  clearing house: not identical property) and the `.sum` DIAGNOSTICS
  name it; before, the two ACBs were pooled and the gain changed
  silently.
- **`list --date` cuts on the project's date basis.** On a settle-basis
  project (the Canadian default) the as-of positions now drop rows by
  settlement date, like the gains year and `t1135`: a sale traded Dec 31
  that settles in January is still held at Dec 31. The banner names the
  basis; `taxjson-gains --as-of` follows `--tax-date`.
- **Schedule 3 outputs name the slip capital-gain lines.** `sum`'s FOR
  THE RETURN block, `form-export` and docs/filing.md now say that
  capital gains on T3 (box 21, line 17600) and T5/T5013 (box 18, line
  17400) slips are not in their rows and are entered from the slips.
- **LSE, ASX and other non-North-American shares settle T+2.** The
  settlement lag followed the US T+1 cycle for every currency but CAD, so
  a GBP or AUD sale on the second-to-last trading day of the year landed
  in that year; GBP/EUR/CHF now move to T+1 on 2027-10-11 and every
  other non-North-American currency stays T+2.
- **A spin-off booked at $0 keeps the checklist open.** The `elections`
  step now needs attention while any taxable spin-off or merger is
  booked at $0 (`fmv_per_share=0`, the "defer" value), and `taxjson
  elect --set ... --hint fmv_per_share=0` says what it books.
- **An option held past its expiry is named.** `taxjson run` warns (on
  the console and in the `.sum` DIAGNOSTICS) for every option a taxable
  account still holds after its expiry date — the export dropped the
  expiry, assignment or exercise row. For a long contract the premium
  paid is an unbooked loss of the expiry year; option-boundary covered
  written contracts only.

## v0.16.0 (2026-09-25)

- **`taxjson sum`: FOR THE RETURN block** — per taxable account, the
  proceeds, cost (ACB), outlays, gain and denied superficial losses a
  return's capital-gains entry asks for (TurboTax: proceeds / ACB /
  outlays), on form-export's Schedule 3 convention; proceeds − ACB −
  outlays equals the allowed gain. Also under `filing` in `--json`.
- Docs: deck and website refreshed for s.49 option timing, `option-boundary`
  and `checklist`; the site's FAQ named a `taxjson filed` command that does
  not exist (it is `close-year` / `check-filed`).
- **`taxjson checklist`**: the filing checklist as a command. Every
  step is auto-detected by running the command that proves it (run,
  sanity, find-missing-history, elect --pending, audit, option-boundary,
  reconcile-slips on `inputs/slips/`, form-export, t1135, check-filed,
  git status); steps no command can prove are confirmed with `--done ID`
  (or `--skip`, `--undo`, `--reset`; marks in `checklist.json`) and a
  mark never hides a later detector finding; `--walk` steps through the
  open items interactively; `--quick` skips the slow detectors; `--json`
  for machines. Exit 1 while anything is open. The report prints a
  `checking ID (command) ...` line on stderr before each slow detector,
  and `--walk` checks one step at a time so the first open step appears
  at once instead of after the whole list (which took a silent minute
  on a big book).
- **`docs/filing.md`**: the filing checklist — freeze inputs, build and
  clean, reconcile to the slips, produce the numbers, file and lock,
  after assessment — each step with the command that proves it. Linked
  from the README workflow and documentation sections.
- **`taxjson init` scaffold**: every switch in one canonical, column-aligned
  layout (`year`, `country`, `province`, `base_currency`,
  `source_currencies`, `tax_date`, then the commented defaults for
  `cross_asset`, `fx_cash_gains` and — Canada only — the three
  `option_*` keys, each with its default and a one-line meaning), and
  `holdings = [...]` shown on the margin account. Two projects' files now
  diff only where their values differ.
- **RBC Direct**: the 2022-vintage export's `10-Jan-22` settlement dates
  parse (they silently fell back to the trade date); a dividend from a
  company with EXP in its name (PEYTO EXPLORATION) is no longer classed
  as a 0-quantity trade — the trade-code tokens match as whole words.
- **`taxjson estimate`**: 2024 rate vintage (federal 15% first bracket,
  BPA 15,705; ON/BC/AB and IRS 2024 tables) so a 2024 project no longer
  runs on the 2025 tables.

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
  rebooked rows; an RRSP move (IB -> Questrade) could carry one
  symbol five times — arithmetically -N, but the two Ca legs read as +N
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
  purchases at the reinvestment price (`REINV@C$7.12500` in the
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
  spurious precision — 43 shares paid $17.85 showed 0.41511628 for a
  dividend declared at 0.415, disagreeing with the same payment in
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
