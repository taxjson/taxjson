# Settings and project files

Every key `taxjson.toml` accepts and every other file a project reads, with its meaning, default, country, when to change it and an example. All examples use made-up symbols and amounts.

A project is a folder:

| Path | What it is | Section |
| --- | --- | --- |
| `taxjson.toml` | the configuration | [taxjson.toml](#taxjsontoml) |
| `ticker.map` | symbol rules and lookups (optional) | [ticker.map](#tickermap) |
| `tobase.map` | Canada: the interlisted master's pairs (a TSX share and its US listings: one security), kept by `taxjson update-tobase-map` | [tobase.map](#tobasemap) |
| `inputs/<account>/*.csv` | broker exports, detected by content. Only `.csv` and `.tt` files are read: an Excel export (`.xlsx`, `.xls`) stops `taxjson run` unless its CSV conversion (`taxjson-xlsx-to-csv FILE.xlsx -o FILE.csv`) sits beside it; a positions-only file (an RBC Holdings Export) is skipped with a note | — |
| `inputs/<account>/*.tt` | hand-entered rows | [.tt files](#tt-files) |
| `inputs/<account>/<file>.csv` + mapping | any other broker's CSV | [Generic importer mapping](#generic-importer-mapping) |
| `inputs/<account>/manifest.json` | your corporate-action elections (keep it) | [manifest.json](#inputsaccountmanifestjson-corporate-action-elections) |
| `inputs/<crypto account>/sends.json`, `crypto_sends.tt` | your crypto-send decisions (keep it) and the sales generated from them (never edit the generated file) | [sends.json](#inputscrypto-accountsendsjson-and-crypto_sendstt) |
| `inputs/<account>/missing_history.tt` | units held before your files start, cost unknown: `OPENING <date> <SYMBOL> <qty> cost=unknown` lines | [OPENING ... cost=unknown](#opening--costunknown) |
| `missing_history.json` (project root) | no longer read (taxjson before v0.27.0; `phantoms.json` before that): every command stops, naming `taxjson migrate`, which converts it into `missing_history.tt` lines | [OPENING ... cost=unknown](#opening--costunknown) |
| holdings TOML (anywhere) | a broker's positions, for `taxjson sanity` / `taxjson opening` | [Holdings TOML](#holdings-toml) |
| `inputs/slips/slips.toml`, `inputs/slips/U*.YYYY.dividends.csv` | your T5 / T3 slips and IB's dividends reports, for `taxjson slip-audit` (the project's own, also when `inputs_dir` names a shared folder). Not an account folder: it needs no `[accounts.slips]` | [inputs/slips/](#inputsslips-slipstoml-and-ibs-dividends-reports) |
| `inputs/slips/*.csv` | T5008 / 1099-B slips, for `taxjson reconcile-slips` | — |
| `holdings/*.toml` | the year's broker positions snapshots, read by `taxjson sanity` with no setting. Optional: with no folder or no file nothing is compared | [holdings/](#holdings-the-years-positions-snapshots) |
| `filed/<year>.json` | close-year locks (keep them) | [filed/YEAR.json](#filedyearjson-close-year-locks) |
| `work/`, `reports/` | rebuilt by every `taxjson run` (`work/`: per-stage files and the price and FX caches; `reports/`: `<account>.sum`, `wash_radar_*`, `fees.rpt`, holdings); never edited. `taxjson init`'s `.gitignore` lists them | — |

Every map file and folder other than `inputs/` is optional. What cannot be rebuilt — `taxjson.toml`, `ticker.map`, `tobase.map`, `inputs/` (its `.tt` files, `manifest.json` and `sends.json` included) and `filed/` — is what to keep in version control or a backup.

The exports can live in a folder every year's project shares: `taxjson init` makes that layout by default — `inputs/<account>/` beside the year folders (`2025/`), each a complete project whose `taxjson.toml` names the shared folder with `inputs_dir = "../inputs"` (see [Folders](#folders); docs/getting-started.md, "One folder of exports for every year"). Each year still has its own `taxjson.toml`, `ticker.map` and results; the exports are shared — with them the `.tt` files, so a `missing_history.tt` line is one record for every year — and, in Canada, `tobase.map` (the interlisted pairs: one file beside the year folders that every year reads, `tobase_map = "../tobase.map"`; each year's ticker.map still wins over it).

Rules every reader applies:

- `taxjson init` writes every key of the project's country, the optional ones commented out with their default; `taxjson format` puts an edited or older file back into that layout without changing what it configures, so two years' files differ only where their values do. The layout (`src/taxjson/lib/config_template.py` — `SETTINGS_GROUPS`, `TABLES`, `PROSE`): `[settings]` first, in the groups below, each under a `## --- Name ---` line with a blank line between groups; then the accounts (a commented reference block naming every account key once, then your `[accounts.NAME]` tables, key lines only, in your order); then `[estimate]`, `[carryover]`, `[[distributions]]` and, in Canada, `[[capital_gains_dividends]]` and `[instalments]` (`[[...]]` entries keep their order). Keys are alphabetical within a group or table (an account's `type` first), set and commented-out keys in one sequence, the `=` signs in one column per table. A description sits on the line(s) above its key; a key's list of values ends its line. Prose comment lines start with `## `; a commented-out key, table or line of a multi-line value starts with a single `# ` and is switched on by deleting it. `taxjson format` sorts your own comment lines the same way: one that reads as TOML keeps `# `, any other becomes `## text`.
- An unknown key warns with a did-you-mean. A key or table the project's country does not own is refused, naming it (Canadian and US settings never mix; the owners are `src/taxjson/lib/country.py` — `SETTING_COUNTRY`, `CONFIG_COUNTRY`).
- Booleans are unquoted: `crypto = "false"` is refused, because a quoted string would read as true.
- Every key below is in the validator's lists: `src/taxjson/lib/config_check.py` — `ACCOUNT_KEYS`, `ESTIMATE_KEYS`, `INSTALMENTS_KEYS`, `CARRYOVER_KEYS`, `DISTRIBUTION_KEYS`, `CGD_KEYS`; `src/taxjson/lib/config_template.py` — `validator_keys`, `SETTINGS_SPEC`, `ACCOUNT_SPEC`, `TABLES`, `SETTINGS_GROUPS`. `tests/test_settings_doc.py` fails when one is missing here.

---

## taxjson.toml

### `[settings]`

The keys come in groups, in the order `taxjson init` writes them.

#### Project

#### `year`
- **Meaning:** the tax year the pipeline reports on.
- **Default:** none — required. An integer from 1900 to next year.
- **Country:** both.
- **Change it when:** you start next year's project (a new folder per year is the usual layout; `taxjson handoff` checks the hand-over).
- **Example:** `year = 2025`

#### `country`
- **Meaning:** whose tax law the project follows: `canada` (or `ca`) or `usa` (or `us`). taxjson never assumes one.
- **Default:** none — required by every command.
- **Country:** both.
- **Change it when:** never in a project with books; changing it makes every report refuse the old books until `taxjson run` rebuilds them.
- **Example:** `country = "canada"`

#### `province`
- **Meaning:** the province `taxjson estimate` taxes at: `ON`, `BC` or `AB`. Quebec and the other provinces are refused.
- **Default:** none. Without it `taxjson close-year` uses a federal-only estimate and says so.
- **Country:** Canada only.
- **Change it when:** you want provincial tax and AMT in the estimate.
- **Example:** `province = "ON"`

#### `tax_date`
- **Meaning:** which date puts a trade in a tax year: `settle` or `trade`.
- **Default:** `settle` in Canada (CRA dates a disposition by settlement), `trade` in the US.
- **Country:** both.
- **Change it when:** almost never. Only to reproduce books filed on the other basis; `taxjson tax-logic` states the rule in force. `.tt` lines carry one date: write the date that matches this setting.
- **Example:** `tax_date = "settle"`

#### `local_timezone`
- **Meaning:** the IANA zone crypto exchange rows (stamped in UTC) are dated in: in `America/Toronto` a Kraken or Coinbase fill at 03:00 UTC on January 1 belongs to the previous tax year. Outside a project the `TAXJSON_LOCAL_TZ` environment variable is used; in a project the setting wins.
- **Default:** none. A project with a `crypto = true` account stops every command until it is set (only `taxjson format`, `format-map`, `migrate`, `init` and `help` still run), naming the key and suggesting this machine's zone; with no coins, delete the scaffold's crypto account instead. `taxjson init` writes this machine's zone when it can read one (not a server's UTC).
- **Country:** both.
- **Change it when:** you set up a crypto account, or you moved. A change re-dates every crypto row and re-keys the crypto sends.
- **Example:** `local_timezone = "America/Toronto"`

#### `prior_year_record`
- **Meaning:** the path to last year's close-year lock, read by `taxjson handoff`, `taxjson carryover`, `taxjson option-boundary`, `taxjson edge-cases` and the carry-forwards of the estimate.
- **Default:** none (then `filed/<year-1>.json` in this project, if any). `taxjson new-year` sets it to the previous year folder's lock.
- **Country:** both.
- **Change it when:** each year's project lives in its own folder.
- **Example:** `prior_year_record = "../2024/filed/2024.json"`

#### Folders

Where the project reads its exports and positions and writes files for other tools. A relative path is read from the project folder; every one must stay inside the folder that holds the project (its parent) — a path or symlink leading further out is refused, naming it (`src/taxjson/lib/project_layout.py` — `folder_setting`, `setting_problems`).

#### `inputs_dir`
- **Meaning:** the folder of the broker exports (`<folder>/<account>/`: CSVs, `.tt` files, `manifest.json`, `sends.json`), when the year projects share one. The run reads it like `inputs/`; a folder of an account this year's `taxjson.toml` does not have (one split later, one closed earlier) is not read, with one `Info:` line. With it, a year folder named `YYYY` must hold that year's project (`year`), and the year's slips stay in the project's own `inputs/slips/` (slips belong to one year; any other folder there is not read, with a warning). `taxjson fetch` downloads into it too, with a note that the download applies to every year.
- **Default:** `inputs/` in the project.
- **Country:** both.
- **Change it when:** `taxjson init` sets it (`"../inputs"`); `taxjson migrate --to-years` sets it on an existing project.
- **Example:** `inputs_dir = "../inputs"`

#### `holdings_dir`
- **Meaning:** the folder of the year's broker positions snapshots `taxjson sanity` (and the end of `taxjson run`) compares the books with, with no `holdings = [...]` setting (see [holdings/](#holdings-the-years-positions-snapshots)). `taxjson fetch --positions` writes `<account>_live_holdings.toml` there.
- **Default:** `holdings/` in the project.
- **Country:** both.
- **Change it when:** your download tool writes the snapshots elsewhere inside the folder holding the project.
- **Example:** `holdings_dir = "holdings"`

#### `exports_dir`
- **Meaning:** where the newest year's full run copies its `reports/<account>_holdings.toml` (positions with their filing cost) and wash radar (`wash_radar_*.json` and `.rpt`), with a `README.txt` naming the year, for other tools to read whatever the year. An older year's run leaves it alone ("newest": no year folder `YYYY` beside this one has a later year).
- **Default:** none: nothing is copied.
- **Country:** both.
- **Change it when:** `taxjson init` sets it (`"../exports"`).
- **Rules:** a folder of its own: one that is, holds or sits inside the inputs folder (shared or the project's `inputs/`), the holdings folder, a year folder, the project's `work/`, `reports/` or `filed/`, or the project folder is refused, naming it (`src/taxjson/lib/project_layout.py` — `exports_overlap`). Each export records the files it wrote in `.taxjson-exports.json` there, and the next removes only those it no longer writes: a file you put in the folder is never touched.
- **Example:** `exports_dir = "../exports"`

#### `tobase_map`
- **Meaning:** the [tobase.map](#tobasemap) every year of a multi-year project reads: one file beside the year folders, so the interlisted pairs are the same for every year and `taxjson update-tobase-map` (in any year folder, or in the folder holding them) updates them once. Each year's ticker.map still wins over it. A change to it is a change to every year's inputs: each year's run banner, `taxjson years` and the checklist name it (`changed: ../tobase.map (shared by every year)`), and a filed year's `check-filed` shows whether it moved the filed figures.
- **Default:** `tobase.map` beside ticker.map (a single-folder project's root).
- **Country:** Canada (a US project reads no tobase.map: tax-logic US-XLIST-05).
- **Change it when:** `taxjson init` sets it in the year layout (`"../tobase.map"`), `taxjson new-year` keeps it, `taxjson migrate --to-years` sets it, and `taxjson migrate` sets it in each year folder when it makes the years' own copies (the layout before v0.27.1) one file.
- **Rules:** like the folder settings, the path must resolve (a symlink followed) inside the folder that holds the project, and it must not name a folder, a file inside this project or one inside another year's folder (`src/taxjson/lib/project_layout.py` — `tobase_setting`, `tobase_map_path`). A year folder that has the setting and still holds a `tobase.map` of its own is refused, naming both (`tobase_both_problem`): `taxjson migrate` makes them one.
- **Example:** `tobase_map = "../tobase.map"`

#### Currencies

#### `base_currency`
- **Meaning:** the report currency.
- **Default:** the country's: `CAD` (Bank of Canada rates) or `USD`. Another currency is refused.
- **Country:** both.
- **Change it when:** never.
- **Example:** `base_currency = "CAD"`

#### `source_currencies`
- **Meaning:** currencies you hold besides the base currency; their exchange rates are fetched.
- **Default:** `["USD"]` in Canada; none in the US (an all-USD project needs nothing).
- **Country:** both.
- **Change it when:** an account or trade is in another currency (a US project with a Canadian listing, a UK listing). A row in a currency with no rates stops the run naming the date and the pair.
- **Example:** `source_currencies = ["USD", "GBP"]`

#### `fx_cash_gains`
- **Meaning:** `true` prints the FX-on-foreign-cash report at the end of `taxjson run` (Canada s.39(1.1) with its $200 exemption; US §988, ordinary income) and writes `reports/fx_cash.rpt`. `taxjson fx-cash` prints it on demand either way. With the default ledger the note says NOT RELIABLE, never a reportable figure (see `fx_cash_ledger`).
- **Default:** `false`.
- **Country:** both.
- **Change it when:** you hold material foreign cash and want the figure every run.
- **Example:** `fx_cash_gains = true`

#### `fx_cash_ledger`
- **Meaning:** which FX-on-cash ledger `taxjson fx-cash`, `taxjson sum`, the checklist and `close-year` use. `v1` reads only the trades and income of the taxable books (no conversion, deposit, withdrawal, margin balance or opening pool), so every output says NOT RELIABLE and shows no reportable figure. `v2` (opt-in, under audit) also reads the cash events — IB Forex trades, Deposits & Withdrawals and Cash Report balances; RBC cash rows; Questrade FX conversions (FXT), deposits, withdrawals, cash-only transfers and stock-lending income (LFJ); Kraken fiat funding, fiat conversions and ledger balances; Coinbase fiat and stablecoin moves and conversions; and the `.tt` lines `FXCONV`, `CASHMOVE`, `CASHOPEN`, `CASHBAL` — models a negative broker balance as a debt in that currency, opens the year with the pool `close-year` recorded for the year before (or `CASHOPEN` lines), reconciles each broker account to its statement balances (tolerance 1.00 unit), and when anything is missing says NOT COMPUTED with each problem (date, account, amount) instead of a figure. An account of a broker whose export it reads no cash event from (Webull, the generic importer) that moves foreign cash is one of those problems until a `CASHBOOK <book> complete` line says the account's `.tt` cash lines are all its conversions, deposits and withdrawals; the report lists such accounts (NOT READ FROM THE EXPORT; `unread_books` in `--json`). Even a computed v2 figure is labelled "v2 (opt-in, under audit)" and the checklist's fx-cash step stays at attention until you mark that figure reviewed (`taxjson checklist --done fx-cash`). `taxjson fx-cash --ledger v1|v2` overrides it for one run (`--cash-events` lists what v2 read). The code is `src/taxjson/lib/fx_cash_v2.py` — `build`; `src/taxjson/lib/cash_events.py` — `collect`.
- **Default:** `"v1"`.
- **Country:** both.
- **Change it when:** you want to audit the full ledger on your books; expect to add `.tt` lines for what no export carries.
- **Example:** `fx_cash_ledger = "v2"`

#### `fx_cash_inflow_cost`
- **Meaning:** ledger v2 only: what foreign cash that comes into the books from outside them (a deposit, a transfer in) costs when no `.tt` `CASHMOVE` line declares it. `declared` refuses it (NOT COMPUTED, naming the line to add); `spot` takes that day's rate for every such inflow — an explicit choice, listed in the report beside each move.
- **Default:** `"declared"`.
- **Country:** both.
- **Change it when:** the foreign cash you deposit was bought on the day (so the day's rate is its cost) and you do not want a line per deposit.
- **Example:** `fx_cash_inflow_cost = "spot"`

#### Options

#### `option_premium_timing`
- **Meaning:** when a written option's premium is taxed: `grant` (a gain in the year written, ITA s.49(1); a buy-back is a loss in its own year) or `close` (netted at the closing transaction).
- **Default:** `grant`.
- **Country:** Canada only (a US premium is taxed at the close, §1234).
- **Change it when:** you filed earlier years on close timing and must stay consistent; see `taxjson option-boundary`.
- **Example:** `option_premium_timing = "grant"`

#### `option_grant_timing_since`
- **Meaning:** contracts written before this year keep close timing — the transition from books filed the old way.
- **Default:** the project's `year` (warned when the books hold a written option; a value later than `year` is warned too).
- **Country:** Canada only.
- **Change it when:** set it once, to the first year you file under grant timing, and keep it unchanged in every later year's project (do not bump it with `year`: `taxjson handoff` flags a written option carried across under another timing, taxed twice or in no return).
- **A new project:** `taxjson init` leaves it commented out (the default, the project's own year), or writes the value another year folder beside it sets; the default is right when your earlier returns netted written options at the close (close timing). If last year's return already reported each premium in the year written (grant timing: a previous taxjson project, or your preparer's), set it to the first year filed that way instead: a contract written last year and bought back or expired this year would otherwise be taxed again at the close. `taxjson run` asks when such a contract exists (the premium at stake and the question); the setting, or `taxjson checklist --done option-boundary` when last year did not report the premiums, answers it — for the contracts asked then (a contract written before the setting and closed later is asked again).
- **Example:** `option_grant_timing_since = 2025`

#### `option_buyback_loss_superficial`
- **Meaning:** `true` applies the strict s.54 reading: a loss on buying back a written option is superficial when identical options are bought within 30 days and still held.
- **Default:** `false` (a buy-back loss is exempt under either timing).
- **Country:** Canada only.
- **Change it when:** your adviser takes the strict reading.
- **Example:** `option_buyback_loss_superficial = false`

#### `leaps_months`
- **Meaning:** options bought more than this many months before expiry count as LEAPS in `taxjson leaps` and `taxjson leaps-sum` (views only, no tax figure).
- **Default:** `9` (a whole number from 1 to 120).
- **Country:** both.
- **Change it when:** you want another cut-off in those views.
- **Example:** `leaps_months = 12`

#### Income

#### `corporate_distributions`
- **Meaning:** Canadian issuers whose "Distribution" / "DIST ON" rows are a corporation's payout, dated when paid (and whose return of capital keeps the pay date), beyond the built-in split-share list. An entry covers every class and series of its root.
- **Default:** none.
- **Country:** Canada only.
- **Change it when:** a corporation's distribution is dated by its record date (the run's `ATTENTION: income year:` line names one whose description says Corp, Inc or Ltd).
- **Example:** `corporate_distributions = ["XYZQ.TO"]`

#### `foreign_return_of_capital`
- **Meaning:** what an IB "(Return of Capital)" from a non-Canadian issuer is: `dividend` (s.90(1)) or `acb` (lowers the shares' ACB). Other brokers always lower the ACB.
- **Default:** `dividend`.
- **Country:** Canada only (a US project always lowers basis, §301(c)(2)).
- **Change it when:** you take the position that the payment is a return of capital.
- **Example:** `foreign_return_of_capital = "acb"`

#### `ric_january_dividends`
- **Meaning:** January fund or REIT dividends received on Dec 31 of the prior year (§852(b)(7), §857(b)(9)): `"SYMBOL"` (every January one) or `"SYMBOL YYYY-01-DD"` (that payment). A bare root is the fund's US listing only.
- **Default:** none; the run warns about a January dividend with an October–December ex or record date.
- **Country:** US only.
- **Change it when:** the warning names a fund whose 1099-DIV puts the payment in the earlier year.
- **Example:** `ric_january_dividends = ["XYZQ.US 2026-01-15"]`

#### Futures

#### `futures_settle`
- **Meaning:** when futures and futures options settle: `trade` (the trade date; variation margin settles the P/L daily) or `next_day` (the clearing premium date).
- **Default:** `trade`.
- **Country:** both.
- **Change it when:** you need the clearing premium date instead (it can move a Dec 31 fill into January under settle dates).
- **Example:** `futures_settle = "trade"`

#### Transfers

#### `transfers_as_acquisitions`
- **Meaning:** how a registered (Canada) or retirement (US) account's transfer in or out is treated. `false`: a move between accounts — its shares count as held, but it is never a purchase for the superficial-loss / wash-sale rule, and the run warns once about each transfer-in near a loss. `true`: every such transfer is a purchase or sale on its date, and an arrival in a loss's window stops the run until declared.
- **Default:** `false`.
- **Country:** both.
- **Change it when:** you want each contribution treated as a purchase and will declare custody moves. With `true` a stopped run is answered in a `.tt` file of the account: a custody move whose history was lost is one `ACQUIRED` line (the real purchase plus a DECLARED counter-transfer); broker churn that nets to zero but sits near a taxable trade is netted by the run itself when three or more symbols of one account share zero-net clusters over a common few days, else it needs a declared zero-net pair (`TRANSFER` ... `DECLARED`, the form the run prints); a genuine contribution from outside the project is a `BUYSELL` dated the contribution day. A contribution from a taxable account in the project needs nothing: it is paired as an in-kind move (see `INKIND`).
- **Example:** `transfers_as_acquisitions = false`

#### Corporate actions

#### `sheltered_elections`
- **Meaning:** what a spin-off or merger in a sheltered account (Canada: RRSP, LIRA, TFSA, RESP, RRIF ...; US: IRA, Roth, 401(k), HSA, 529 ...) does when it has no saved election. `"zero"`: it is booked without asking — a spin-off's new shares at $0 cost with the parent keeping its whole cost, a merger's new shares taking the old shares' cost — with one `Info:` line per run; it is never pending, so `run --strict`, `elect --pending` and the checklist do not wait for it. `"ask"`: it is asked like a taxable account's event (the prompt, `elect --pending`, exit 3 without a terminal). Either way an election saved with `taxjson elect` wins, and a taxable account is always asked. No tax depends on it: nothing is taxed inside the account, an in-kind move in or out is valued at fair market value, and the superficial-loss / wash-sale rule counts the account's units, not their cost (tax-logic CA-CORP-11 / US-CORP-12). The spun-off shares count as acquired for the superficial-loss rule (Canada, as under every spin-off election); in a US project they are not a wash-sale purchase (as under `tax_free_355`) unless you elect `taxable_distribution_301`.
- **Default:** `"zero"`.
- **Country:** both.
- **Change it when:** you want the sheltered holdings view to show a real cost for every spin-off, and to be asked for it.
- **Example:** `sheltered_elections = "ask"`

#### Retired setting

#### `cross_asset`
- **Meaning:** none any more; still recognised only to warn that it is ignored. Delete it.
- **Country:** both.

### `[accounts.NAME]`

One table per folder under `inputs/` (the folder name is the account name). Names use letters, digits, `_`, `-` and `.`; `COMBINED`, `sheltered`, a name ending in a pipeline suffix (`_raw`, `_base`, `_gains`, `_wash`, `_merged`, `_sorted`, `_filled`, `_mapped`, `_report`, `_tt`, `_manifest`, `_sources`) and a name inside another account's work files (`margin_ib` beside `margin`) are refused.

#### `type`
- **Meaning:** `taxable` (on your return) or `sheltered` (a registered or retirement plan: tracked, kept out of the filing totals, counted for the superficial-loss / wash-sale rule).
- **Default:** none — required; an untyped account is refused.
- **Country:** both.
- **Change it when:** to add a spouse's or controlled corporation's account so their purchases deny your losses, declare it `sheltered` (it is then listed as if it were your plan).
- **Example:** `type = "taxable"`

#### `plan`
- **Meaning:** the plan kind when the account name does not say it. Canada: `tfsa`, `rrsp`, `rrif`, `lira`, `lif`, `lrif`, `fhsa`, `resp`, `rdsp`, `prpp`; US: `ira`, `roth`, `401k`, `403b`, `457b`, `sep`, `hsa`, `529`. The other country's plans are refused.
- **Default:** from the name.
- **Country:** both (each its own plans).
- **Change it when:** a registered account has a neutral name; `taxjson tips` uses it (a US dividend payer in a TFSA is flagged).
- **Example:** `plan = "tfsa"`

#### `crypto`
- **Meaning:** `true` for a Coinbase or Kraken account: crypto prices are filled, rows are dated in `local_timezone`, and the account's dispositions are crypto-assets (Canada Schedule 3 line 7 from 2025; US Form 8949 boxes G–L from 2025, no wash-sale rule).
- **Default:** `false`.
- **Country:** both.
- **Change it when:** the folder holds exchange exports. A crypto file in an equity account (or the reverse) is refused.
- **Example:** `crypto = true`

#### `transfers`
- **Meaning:** `true` keeps the broker's TRANSFER rows (contributions, withdrawals) in the books; `false` keeps them aside as custody evidence (`taxjson transfers`).
- **Default:** `false`.
- **Country:** both.
- **Change it when:** a registered account's contributions and withdrawals move shares in and out (the usual setting for an RRSP or TFSA). Keep `false` on a taxable account: a taxable book's TRANSFER rows that do not cancel out are refused (replace them with the purchase history). In a US project `false` also pairs moves between your own taxable accounts so the lots travel with their dates.
- **Example:** `transfers = true`

#### `holdings`
- **Meaning:** positions files `taxjson sanity` reconciles against with no arguments (IB statements, RBC Holdings Exports, holdings TOMLs); a full `taxjson run` also checks them at the end and warns, never fails. Without it, the account's snapshots in the year's [holdings/](#holdings-the-years-positions-snapshots) folder are found by themselves.
- **Default:** none.
- **Country:** both.
- **Change it when:** you keep the broker's year-end positions.
- **Example:** `holdings = ["~/broker/margin_holdings.toml"]`

#### `broker_accounts`
- **Meaning:** the broker account ids of this account's statements, when there are several (one taxjson account spanning two IB accounts): a `holdings/` snapshot whose `[meta] account` is one of them, or `account`, is this account's. Compared by letters and digits, case-insensitive; shown masked.
- **Default:** none (`account` alone).
- **Country:** both.
- **Change it when:** a snapshot is named for its broker account rather than for the taxjson account.
- **Example:** `broker_accounts = ["ACCOUNT_A", "ACCOUNT_B"]`

#### `combined_broker_accounts`
- **Meaning:** every row of an account folder is booked to that one taxjson account. When a statement spans several broker accounts (an IB statement or Flex query covering two IB accounts, statements of different IB accounts in one folder, a Questrade or RBC export holding two accounts' rows), `taxjson run` prints an ATTENTION line naming them (ids masked to their first two characters): right only when they are one tax entity — a TFSA exported with a margin account would land in the wrong book. `true` declares that every broker account in this folder's statements is yours and taxable together; the ATTENTION becomes a one-line note with the masked ids.
- **Default:** `false` (an unquoted `true` / `false`).
- **Country:** both.
- **Change it when:** one export covers two of your taxable accounts (a second IB account exported with the main one). On a `sheltered` account it is refused unless the statement itself shows every account is the same registered plan (Questrade's Account Type column); IB and RBC statements do not name the plan, so there the parse stops — export each plan into its own folder. A Questrade export mixing a registered plan and a taxable account is refused with or without it (`src/taxjson/lib/brokerages/base.py` — `combined_accounts_note`, `combined_accounts_refusal`).
- **Example:** `combined_broker_accounts = true`

#### `exercise_fee`
- **Meaning:** Webull's exercise/assignment charge on the stock leg. A $0 option close beside a stock trade at the strike carrying exactly this charge is paired as an exercise or assignment.
- **Default:** none: no pair is inferred, each candidate is named.
- **Country:** both.
- **Change it when:** you had an option exercised or assigned at Webull.
- **Example:** `exercise_fee = 1.00`

#### `year_end_posting`
- **Meaning:** RBC: the day of the next year (`MM-DD`) by which the year's Dec 31 book-cost rows are posted. An export taken earlier gets a note that they may be missing.
- **Default:** `"06-30"`.
- **Country:** both.
- **Change it when:** RBC posts later than that for your funds.
- **Example:** `year_end_posting = "07-15"`

#### `brokerage`
- **Meaning:** the `taxjson fetch` source of this account (a fetcher plugin's name; `taxjson fetch --list` names the installed ones). Accepted whether or not a plugin is installed.
- **Default:** none.
- **Country:** both.
- **Change it when:** you download this account with a fetcher plugin.
- **Example:** `brokerage = "SOURCE"`

#### `account`
- **Meaning:** the broker account id `taxjson fetch` downloads, if its source needs one.
- **Default:** none.
- **Country:** both.
- **Example:** `account = "ACCOUNT_ID"` (keep the real id out of anything you share).

#### `query_id`
- **Meaning:** the report (query) id `taxjson fetch` runs, if its source needs one.
- **Default:** none.
- **Country:** both.
- **Example:** `query_id = "QUERY_ID"`

### `[estimate]`

Inputs `taxjson estimate` (and, in Canada, the instalments current-year basis) uses when the command-line flags are not given; a flag wins.

#### `other_income`
- **Meaning:** income besides these books (employment, interest ...).
- **Default:** `0`.
- **Country:** both.
- **Example:** `other_income = 900`

#### `other_losses`
- **Meaning:** Canada: net capital losses of earlier years applied, in full dollars (netted against the year's gains before the inclusion, up to them). US: the short-term capital loss carryover.
- **Default:** the net capital loss (US: the short-term carryover) the latest close-year lock before the project year carried out (`filed/<year>.json` or `prior_year_record`); with no lock, `0`. `taxjson estimate` prints where the figure came from.
- **Country:** both (meaning differs).
- **Example:** `other_losses = 500`

#### `deductions`
- **Meaning:** lines 20700–23500 the AMT allows in full (RRSP, FHSA, RPP ...).
- **Default:** `0`.
- **Country:** Canada only.
- **Example:** `deductions = 600`

#### `carrying_charges`
- **Meaning:** line 22100 (margin interest and other carrying charges); deducted in full from regular income, at 50% in the AMT base.
- **Default:** `0`.
- **Country:** Canada only.
- **Example:** `carrying_charges = 250`

#### `amt_carryover`
- **Meaning:** the minimum tax carryover by year of origin (from the notice of assessment or T691), `{ YEAR = AMOUNT }`. Applied only in the 7 years after it arose.
- **Default:** the carryover the latest close-year lock before the project year recorded; with no lock, none.
- **Country:** Canada only.
- **Example:** `amt_carryover = { 2023 = 120.50 }`

#### `long_term_losses`
- **Meaning:** the long-term capital loss carryover (Schedule D line 14); `other_losses` is then the short-term one.
- **Default:** the long-term carryover the latest close-year lock carried out; with no lock, `0`.
- **Country:** US only.
- **Example:** `long_term_losses = 400`

### `[carryover]`

#### `claimed`
- **Meaning:** net capital losses actually applied on each filed return, by year, `{ YEAR = AMOUNT }` (`taxjson carryover`; the US ledger otherwise assumes the $3,000 offset was used).
- **Default:** none.
- **Country:** both.
- **Example:** `claimed = { 2024 = 400.00 }`

### `[[distributions]]`

One table per non-cash fund distribution (a reinvested capital-gains distribution, usually each December, that no broker export shows; a return-of-capital factor a fund publishes after the year end). `taxjson run` books each as a cost adjustment on the shares every taxable account holds on the record date (units an `OPENING ... cost=unknown` line opens count as held), on the ticker that held them that day (renames and ticker changes followed). Only the cost side is booked: the distribution's income is on the T3/T5 slip, and taxjson adds it neither to the estimate nor to `divs-sum` (the run's note says so). A symbol and date entered in two tables are both applied (they add), with a warning; a return-of-capital entry dated like a return-of-capital ADJUST the books already carry is warned about (the cost would be cut twice: keep one). A malformed entry stops every command. Both countries.

#### `symbol`
- **Meaning:** the books' symbol (matched through ticker.map and renames).
- **Example:** `symbol = "XYZQ.TO"`

#### `record_date`
- **Meaning:** the record date, a TOML date (or a quoted `"YYYY-MM-DD"`; anything else stops every command).
- **Example:** `record_date = 2025-12-29`

#### `per_share`
- **Meaning:** the amount per share, a plain decimal in the project's base currency (the books it adjusts are already converted: convert a US-listed fund's USD factor at the record date's rate first); negative = return of capital; `0` is a placeholder and is not applied.
- **Example:** `per_share = 0.25`

### `[[capital_gains_dividends]]`

T5 box 18 capital-gains dividends the books carry as dividends (no export labels them: IB prints "(Ordinary Dividend)", RBC and Questrade a plain dividend; copy them from the slip): `taxjson divs-sum` lists them under CAPITAL-GAINS DIVIDENDS, apart from the dividend totals, and the estimate (`taxjson estimate`, `taxjson sum --other-income`) moves them from the grossed-up eligible dividends into capital gains. The ACB does not change. One table per payment or per year. An entry that matches no dividend, an amount above the matching dividends or matches in two currencies stops the view naming the entry (`[[capital_gains_dividends]] #2`); a malformed or repeated entry stops every command. Canada only (a US project refuses the table).

#### `symbol`
- **Meaning:** the books' symbol; a bare root covers only its Canadian listings (`"ABCX"` is ABCX.TO, not ABCX.PR.B.TO or ABCX.US).
- **Example:** `symbol = "ABCX.TO"`

#### `year`
- **Meaning:** every dividend of the symbol whose tax date is in that year (instead of `date`; a trust distribution dated by its record date counts in the record date's year).
- **Example:** `year = 2025`

#### `date`
- **Meaning:** one payment's pay date, or its record date when the books date it by the record date (instead of `year`).
- **Example:** `date = 2025-06-16`

#### `amount`
- **Meaning:** `"all"` or the box 18 amount in the dividend's currency (the total over the matching payments, shared pro rata).
- **Example:** `amount = "all"`

#### `account`
- **Meaning:** restrict the entry to one account.
- **Default:** the taxable accounts.
- **Example:** `account = "margin"`

### `[instalments]`

CRA instalments (`taxjson instalments`, and a summary in `taxjson estimate`). Canada only; US estimated tax is not modelled.

#### `basis`
- **Meaning:** `current_year`, `prior_year` or `cra_reminder`.
- **Default:** `current_year`. `prior_year` needs `prior_year_net_tax`; `cra_reminder` needs both net-tax keys.
- **Example:** `basis = "current_year"`

#### `withheld`
- **Meaning:** tax withheld at source this year.
- **Default:** `0`.
- **Example:** `withheld = 300`

#### `prior_year_net_tax`
- **Meaning:** last year's net tax owing as CRA's instalment chart defines it (lines 42000 + 42200 + 42800 (+ 43200) minus 43700 and the refundable credits — not line 48500).
- **Default:** none. Supply it even on `current_year`: a 0 reads as "I owed nothing"; a year not given is assumed to meet the prior-year test.
- **Example:** `prior_year_net_tax = 950`

#### `second_prior_net_tax`
- **Meaning:** the year before's net tax owing.
- **Default:** none.
- **Example:** `second_prior_net_tax = 800`

#### `prescribed_rate`
- **Meaning:** CRA's overdue-tax rate as a decimal fraction (0.07 = 7%).
- **Default:** CRA's published quarterly rates, built in.
- **Change it when:** a rate is newer than the built-in table. Not together with `prescribed_rates`.
- **Example:** `prescribed_rate = 0.07`

#### `prescribed_rates`
- **Meaning:** a dated schedule, a list of `{ from = "YYYY-MM-DD", rate = R }` (CRA resets the rate quarterly).
- **Default:** the built-in table.
- **Example:** `prescribed_rates = [{ from = "2025-01-01", rate = 0.08 }, { from = "2025-07-01", rate = 0.07 }]`

#### `paid`
- **Meaning:** instalments paid, a list of `{ date, amount }` tables with an optional `note`. A payment made before January 1 counts only with `tax_year = YEAR` on its row (credited from January 1). A payment outside the year, or a negative amount, is refused.
- **Default:** none.
- **Example:** `paid = [{ date = "2025-03-15", amount = 250 }, { date = "2024-12-20", amount = 100, tax_year = 2025, note = "early" }]`

---

## ticker.map

The project's one mapping file, at the project root; one rule per line, symbols case-insensitive, notes after `#`. `taxjson run` refuses a map with a malformed line, a rename cycle, one symbol renamed to two targets, a `DISTINCT` pair the renames pool together, or a rename-type line (`GLOBAL`, `TOBASE`, `JOURNAL`, `RENAME`) that joins an option contract or a future with a share listing, an option with a future, or two different option contracts (another expiry, right, strike or market); a respelling of one contract (the same expiry, right, strike and market, the root spelled otherwise: `GLOBAL ZZB.B250620C00010000.TO ZZB250620C00010000.TO`) is allowed. Symbols are matched exactly, suffix included: `GLOBAL ZZOL ZZNW` does not touch `ZZOL.US`. Rules chain (`GLOBAL ZZOLD.US ZZNEW.US` plus `TOBASE ZZNEW.US ZZNEW.TO` sends ZZOLD.US to ZZNEW.TO). An option follows its underlying's line through its root, except where that would land on a contract code the books already trade on the other listing (a USD-strike US option and a CAD-strike Montreal option are different property): the US contract keeps its symbol, with a warning, unless a rule names the option itself (`src/taxjson/bin/taxjson_ticker_map.py` — `guard_option_listing_collisions`). `taxjson ticker-map --suggest --write` appends suggested lines. ticker.map holds standing truths only: a ticker change or a journal between two listings happens on a date and is a `.tt` line of an account, date first (`RENAME <date> OLD NEW`, `JOURNAL <date> FROM TO <qty>`; see [.tt files](#tt-files)). A map that still has the legacy `JOURNAL` or dated `RENAME` lines works (`JOURNAL` is read as `TOBASE`), with one Warning per run. `taxjson format-map` lays the file out the way `taxjson init` writes it: a short header, then the rules in groups, each under a `## --- <Group> ---` heading — Spellings (`GLOBAL`, an undated `RENAME`), Listings of one security (`TOBASE`, `DISTINCT`), Clean-up (`DELETE`), Lookups (`QUOTE`, `EXTRACT`, `CRYPTO`, `T1135` and the market lists), then Retired (`TRADINGVIEW`) and Unrecognized (lines `taxjson run` cannot use, kept as written) when there are any. It also migrates the dated events: each `JOURNAL A B` becomes `TOBASE A B`, and each dated `RENAME OLD NEW YYYY-MM-DD [late=…]` moves, with its comments, to `inputs/<account>/renames.tt` as `RENAME YYYY-MM-DD OLD NEW [late=…]` — in the first account whose books carry the change (else whose books hold the symbols, else the only account, else the first taxable one), among the accounts whose own `.tt` lines of the change agree with its `late=`, one per account kind whose books hold the symbols: a `.tt` RENAME applies to every account of its kind. When that home would change some account's `late=` (an account with no line of its own follows the map line's), the line goes to the first account whose copy keeps every account's choice; when none does, nothing is written and the refusal names each such account with the line to add to its own `.tt` file. An account that did not hold OLD before the date never follows the event's `late=` and is not compared on it. Before writing, it simulates the run: the rename events booked after the move (the project's `.tt` lines plus the moved ones) must be the ones booked now in every account (an account without books yet: on every change that still reaches its kind), or nothing is written (exit 2, naming the lines that disagree); a moved line repeating a `.tt` line written differently is reported. Every target is checked first (a link to outside the project — the file or its `inputs/<account>` folder — is refused), the `.tt` files are written before the map, and a block a file already holds is not added again. The dry run shows the map diff and the `.tt` additions, and a `.tt` `JOURNAL` line for each migrated `JOURNAL` whose listings the holdings show long and short; `--check` fails while the map is not formatted, a migration is pending or `taxjson run` refuses the map. A header an earlier taxjson version wrote (any `taxjson init` template, the earliest projects' header, `taxjson migrate`'s line; re-wrapped, re-cased or with your notes directly below it) is replaced by the current one; a copy you edited is kept, with an Info line, and so is a comment of yours that still describes `JOURNAL` or a dated `RENAME` as a map rule. Your order is kept within a group, a comment directly above a line moves with it, and the parsed map (plus the moved lines) must mean the same (`src/taxjson/lib/ticker_map_format.py` — `format_map`, `GROUPS`, `Moved`; `src/taxjson/lib/dated_events.py` — `home_accounts`, `plan_migration`). The parsers are `src/taxjson/bin/taxjson_ticker_map.py` — `_parse_map_file`, `merge_renames`; `src/taxjson/lib/ticker_map.py` — `parse_side_line`, `RENAME_KEYWORDS`, `SIDE_KEYWORDS`, `MARKET_KEYWORDS`.

### Rename rules

#### `GLOBAL`
- **Form:** `GLOBAL FROM TO`
- **Meaning:** a plain rename in every stage (a security's true ticker). Between two bare crypto codes it is applied by the Coinbase and Kraken parsers before a row is read: it folds a staked or wrapped code into its coin (taxjson ships no such fold), or adds a Kraken legacy code.
- **Country:** both.
- **When:** a broker's odd spelling, a Questrade internal code the run could not resolve, a coin alias.
- **Questrade internal codes:** Questrade's website export writes the rows of shares transferred in from another broker under a code (one letter and digits, `X000123`) instead of the ticker. `taxjson run` resolves a code to a ticker from the account's own trades described like it, else from the transfer it arrived by (another broker's transfer out of the same quantity, from 10 days before the arrival to 3 after, under an agreeing name), else from exactly one listing elsewhere in your books whose name equals it; the share designators (warrant, right, preferred, the class letter) must agree over all of the code's descriptions. Every row of the code is then booked under that ticker, said in one note per account; `taxjson transfers` lists the codes. A code nothing identifies keeps an ATTENTION line, with a near miss and its `GLOBAL` line when there is one: add the line. Any ticker.map rule naming the code wins over the inference (`src/taxjson/lib/symbol_codes.py` — `resolve`, `PAIR_DAYS_BEFORE`, `PAIR_DAYS_AFTER`; tax-logic CA-ACB-CODES / US-BASIS-CODES).
- **Example:** `GLOBAL X000123.US ZZQ.US` · `GLOBAL ETH2 ETH`

#### `TOBASE`
- **Form:** `TOBASE FROM TO`
- **Meaning:** two listings of one security (a US line and its TSX line) are one ACB pool / one security; applied when converting to the base currency. The raw holdings view keeps them apart except where the broker's transfer rows prove a move.
- **Country:** both.
- **When:** an interlisted share you trade on both lines that the run did not join itself (`taxjson ticker-map --suggest` proposes it; `taxjson journals` lists each journal between the two listings, the line that pools them, or why it is not joined).
- **Example:** `TOBASE ZZQ.US ZZQ.TO`

#### `JOURNAL` (legacy)
- **Form:** `JOURNAL FROM TO`
- **Meaning:** read as `TOBASE FROM TO` (one security), with one Warning per run; `taxjson format-map --write` rewrites it. It no longer nets the two listings in the holdings view: a journal's own transfer legs move the units — the broker's (a Questrade BRW pair, RBC's TFR legs, IB's InterDepot) or, when the export lacks them, a `.tt` line `JOURNAL <date> FROM TO <qty>`. The missing-history checks read a journal the books show (a join of the run, a Questrade pair, RBC's J~ reference on the two transfer legs, a `.tt` JOURNAL line) with that day's buys first. `taxjson journals` lists every broker journal between two listings with the line that pools it (yours, `ticker.map:N`, or the run's own join).
- **Country:** both.
- **Example:** `JOURNAL ZZG.U.TO ZZG.TO` (write `TOBASE ZZG.U.TO ZZG.TO`)

#### `DELETE`
- **Form:** `DELETE SYMBOL`
- **Meaning:** drop every row of a pure artifact symbol. A position with real shares is warned about loudly (its cost basis is discarded).
- **Country:** both.
- **Example:** `DELETE ZZTEMP.TO`

#### `DISTINCT`
- **Form:** `DISTINCT A B`
- **Not needed for look-alike tickers:** taxjson never joins two listings because their letters match. Use it only to undo a join taxjson made from a broker's journal evidence, or to answer a `ticker-map --suggest` pair.
- **Meaning:** two listings are separate securities; changes no symbol, undoes an automatic cross-listing join and records the decision. When you need it: when taxjson would otherwise join or ask about the pair — a journal it pairs, a look-alike rename, the cross-listing loss warning, or a MAP-GAP pair of `taxjson ticker-map --suggest` — and they are two securities. It silences any of these for the pair. Not needed when the exports already show the two apart: `taxjson ticker-map --suggest` asks nothing about a Canadian line named as a depositary receipt (a CDR) or on a receipt venue, or a pair whose names are two companies'; nor, in Canada, about a CDR and its US share the interlisted master knows (tobase.map writes no `DISTINCT` line for it since v0.27.1: a journal between the two is never joined, and neither the loss warning nor a MAP-GAP pairs them). `taxjson journals` lists a journal between the two as refused, naming this line. A symbol written bare (no market suffix) beside a share listing also names its US listing, as the books spell a broker's bare US ticker: `DISTINCT ZZR ZZR.TO` keeps `ZZR.US` and `ZZR.TO` apart too (the pair as written stays: a bare symbol may be a coin or a broker's code), with an Info line when the books hold `ZZR.US` and not `ZZR`. A `GLOBAL`, `TOBASE`, `JOURNAL` or undated `RENAME` line written that way is not re-read (the line would move pools): when the books hold the US listing and not the bare symbol, `taxjson run` warns that it joins nothing, naming the line to write (`src/taxjson/bin/taxjson_ticker_map.py` — `distinct_spellings`, `listing_spelling_notes`).
- **Country:** both.
- **Example:** `DISTINCT ZZR.TO ZZR.US`

#### `RENAME`
- **Form:** `RENAME OLD NEW` (undated); legacy: `RENAME OLD NEW YYYY-MM-DD [late=fold|late=separate]`
- **Meaning:** without a date, `GLOBAL OLD NEW`. The dated form is legacy: a ticker change on a date is a `.tt` line `RENAME YYYY-MM-DD OLD NEW [late=…]` (the `.tt` RENAME action below). A dated line here still works the same way, with one Warning per run; `taxjson format-map --write` moves it to a `.tt` file. `taxjson renames` names each dated rename's source (a broker row, an IB contract id, a `.tt` line, a legacy ticker.map line) and lists the look-alike renames the exports show as suggested.
- **Country:** both.
- **Example:** `RENAME ZZOLD.US ZZNEW.US` · legacy `RENAME ZZOLD.US ZZNEW.US 2025-04-01 late=fold`

### Lookups (change no symbol in the books)

#### `QUOTE`
- **Form:** `QUOTE SYMBOL YAHOO_SYMBOL [QTY_RATIO]`
- **Meaning:** the Yahoo Finance spelling `taxjson harvest` and the price chain quote; QTY_RATIO (default 1) converts the position's quantity into the quoted ticker's units.
- **Country:** both.
- **Example:** `QUOTE ZZOLD.TO ZZNEW 0.25`

#### `CRYPTO`
- **Form:** `CRYPTO SYMBOL YAHOO_ID`
- **Meaning:** the Yahoo id of a coin whose ticker collides with another asset; fill-crypto, `taxjson crypto-sends` and `taxjson harvest` quote `YAHOO_ID-USD`. These lines are the only coin ids (taxjson carries no built-in table): without one a coin is quoted as `SYMBOL-USD`. Yahoo gives a ticker two assets share a number (`SYMBOL<number>-USD`): find your coin's id on finance.yahoo.com and write it here. A trailing `-USD` is accepted. Editing a line re-prices under `run --fast`.
- **Country:** both.
- **When:** the run names a coin whose lookup failed (with this line to add), whose Yahoo close is more than 5 times off its own trade prices within 7 days, or that is priced as `SYMBOL-USD` while the price cache holds a numbered id of the same ticker from earlier runs (ATTENTION lines on the console and in the account's `.sum`).
- **Example:** `CRYPTO ZZC ZZC12345`

#### `EXTRACT`
- **Form:** `EXTRACT DESCRIPTION WORDS | CURRENCY | SYMBOL`
- **Meaning:** a parser symbol override: a broker row whose description contains the words (whole words, any case) and whose currency is CURRENCY (`*` = any) gets SYMBOL. First matching line wins; options and futures are never rewritten.
- **Country:** both.
- **When:** the currency-to-suffix rule mislabels a security (a TSX-only USD unit). `taxjson run` warns when a `.US` symbol carries such a unit beside another company ("names two securities") and `taxjson ticker-map --suggest` offers the line, its words the shortest run common to every description of the fund in the project and in no other row's (the words must match the broker's description as written: `US DLR` and `U S DLR` are different words).
- **Example:** `EXTRACT Sample US Dollar Fund | USD | ZZD.U.TO`

#### `T1135`
- **Form:** `T1135 SYMBOL COUNTRY`
- **Meaning:** the T1135 domicile of SYMBOL where its listing suffix is wrong: an ISO 3166 alpha-3 code, or `CA` / `CAN` / `CANADA` / `EXCLUDE` for not specified foreign property. It follows the symbol through a rename; any other value stops the report. An option on a listing a `TOBASE` line pools under a Canadian listing is booked under that listing's code and read as Canadian (taxjson's filing position, tax-logic CA-RPT-17); a `T1135` line on the contract's booked symbol sets another country.
- **Country:** Canada (read by `taxjson t1135`).
- **Example:** `T1135 ZZQ.US CA`

### Market lists (extend or override the shipped data)

The lists taxjson cannot read from an export ship in `src/taxjson/data/markets.toml` (read only by `src/taxjson/lib/markets.py` — `data`, `overrides`). Whenever a built-in list decides an outcome the run prints one note per symbol naming the line that would change it. The file also states a listing convention: `[usd_unit_class]` makes a Canadian-listed fund's US-dollar units `ROOT.U.TO` (suggested for a symbol collision, and used for a Questrade currency journal's USD leg when the account's own rows show no USD listing of the security; an `EXTRACT` or `GLOBAL` line overrides it for one security; `src/taxjson/lib/markets.py` — `usd_unit_listing`). These ticker.map lines change one entry each:

#### `STABLE`
- **Form:** `STABLE SYMBOL USD` or `STABLE SYMBOL NO`
- **Meaning:** a US-dollar stablecoin, or not one. The shipped stablecoins are the `usd_stablecoins` list of `src/taxjson/data/markets.toml` (one list for Kraken and Coinbase alike, read through `src/taxjson/lib/markets.py` — `is_usd_stablecoin`); this line adds a coin to it or removes one. In a Canada project a stablecoin is US-dollar cash (an approximation: a fill more than 2% off 1.00 USD is an ATTENTION line, the de-peg left out of the gains; a fill valued in another currency is converted at the run's rates first, and is said to be unchecked only when no rate exists); in a US project it is property like any coin (a swap, reward or fee in one valued at its 1.00 USD par).
- **Country:** both (the meaning differs).
- **Example:** `STABLE ZZUSD USD`

#### `SPLITSHARE`
- **Form:** `SPLITSHARE ROOT` or `SPLITSHARE ROOT NO`
- **Meaning:** a Canadian split-share corporation: its distributions are a corporation's dividends, dated when paid; `NO` removes a built-in one.
- **Country:** Canada.
- **Example:** `SPLITSHARE ZZS`

#### `INDEXOPT`
- **Form:** `INDEXOPT ROOT` or `INDEXOPT ROOT NO`
- **Meaning:** a broad-based index option root (§1256): kept off Form 8949 and listed for Form 6781.
- **Country:** US.
- **Example:** `INDEXOPT ZZX`

#### `EVENING`
- **Form:** `EVENING ROOT` or `EVENING ROOT NO`
- **Meaning:** an option root with a Cboe evening session: a fill from 20:15 ET is dated the next trading day.
- **Country:** both.
- **Example:** `EVENING ZZX`

#### `MULT`
- **Form:** `MULT SYMBOL N`
- **Meaning:** an option's contract size where the export does not state it (SYMBOL is the option or its root); used for assignments and as the replacement size in the superficial-loss / wash-sale rule.
- **Country:** both.
- **When:** the run notes a contract size ASSUMED 100 for an adjusted series or a mini.
- **Example:** `MULT ZZQ1 50`

#### `VENUE`
- **Form:** `VENUE IBCODE SUFFIX` or `VENUE IBCODE NO`
- **Meaning:** an IB "Listing Exch" code and the listing suffix its lines get (a suffix taxjson knows), or drop a built-in one.
- **Country:** both.
- **Example:** `VENUE ZZEX L`

#### `TRADINGVIEW` (retired)
- **Meaning:** the removed TradingView export's line; ignored, with one note per run asking you to delete it.

---

## tobase.map

Canada only for now. The pairs of the **interlisted master** that ships with taxjson (`src/taxjson/data/interlisted.toml`): Canadian shares that also trade in the United States under the same share class — a TSX or TSX Venture line and its US exchange listing (NYSE, Nasdaq, NYSE American, NYSE Arca) and its US over-the-counter listings (the `...F` ordinary-share symbols; never FINRA's temporary `...D` symbol of a new issue or reverse split), and a TSX fund's US-dollar line of the same units (`X.U` beside `X`, booked under the CAD line: `TOBASE DLR.U.TO DLR.TO`; a `.U` line with a share-class FIGI of its own — a separate series — is not paired). Coverage: the TSX and TSX Venture lines only (Cboe Canada and the CSE are not covered). Each pair is checked against OpenFIGI (the same share-class FIGI on both sides) and an exchange listing against the Nasdaq Trader symbol directory; a depositary receipt (a CDR in Canada, an ADR or ADS in the US) has a share class of its own and is never paired. The master is keyed by share-class FIGI and holds no ISIN or CUSIP; it is rebuilt by the maintainer (`scripts/build_interlisted.py`: the network fetch is a maintainer step into a cache outside the repository, the build itself offline and append-only — an entry is never deleted, an ended one gets `until`).

- **Where:** in a multi-year project, one file beside the year folders that every year reads ([`tobase_map`](#tobase_map) `= "../tobase.map"`); in a single-folder project, beside ticker.map. `taxjson init --country canada` writes it (at the top in the year layout, setting the year's `tobase_map`), `taxjson new-year` keeps the setting (no per-year copy), `taxjson update-tobase-map` keeps it in step with the installed master (dry run by default, `--write` applies it, the previous file kept as `tobase.map.bak`; in a year folder that reads the shared file, or in the folder holding the years, it updates that one file and says it applies to every year). A US project gets none (and does not read one: tax-logic US-XLIST-05).
- **Per-year copies (before v0.27.1):** `taxjson migrate` in the folder holding the year folders (or in any year folder) makes them one: identical copies become one `tobase.map` there at once, each year's `taxjson.toml` gains `tobase_map = "../tobase.map"` and each copy is removed, kept as `tobase.map.bak`. Copies that differ are listed — the lines of yours (your own lines, an edited marked line) that only another copy has, and how many master lines of another master version — and nothing is written without `--write`, which keeps the newest year's file (the lines listed are not carried over: add the ones you still need). `--dry-run` shows the plan. Until then the years keep a copy each (`taxjson years` says so); `new-year` from such a year copies it on, with a note naming `migrate`.
- **Form:** a header (the taxjson version, the master's sources and a `# master-generated: YYYY-MM-DD` line; for a shared file, that it applies to every year), then `TOBASE US CA` lines in sections — `## --- Exchange-listed pairs (one security, one ACB pool) ---`, then `## --- OTC listings of the same shares ---`. No `DISTINCT` line: look-alike listings are never joined, and a depositary receipt the master knows stays apart from its US share without one (the `DISTINCT` lines earlier versions wrote are retracted by `update-tobase-map`: "not needed: look-alike listings are never joined"; one you edited is kept and flagged ATTENTION). Each generated line ends `# master:<share-class FIGI>`; an ended interlisting adds `until=YYYY-MM-DD` (or `until=unknown`), and `ended=SYMBOL` when only that listing ended. Example: `TOBASE QZAB.US QZA.TO  # master:BBG000000Q01`. A pair is booked under the issuer's home listing: the Canadian line, or for a company the master knows is domiciled outside Canada (its `domicile`, the country of its share class's ISINs) the US exchange line (`TOBASE QZC.TO QZCU.US`), so its dividends and its T1135 status stay foreign. An issuer whose domicile the master does not know is booked under the Canadian line: a `T1135` line in ticker.map corrects its T1135 country.
- **Meaning:** read with ticker.map as if its lines were written there (tax-logic CA-XLIST-06): the two listings are one ACB pool and one security for the superficial-loss rule. **ticker.map wins:** a pair is not applied when a ticker.map rule decides one of its listings — a `TOBASE`, `JOURNAL`, `GLOBAL`, `DELETE` or dated `RENAME` of either listing, a `TOBASE` that books another listing under the one the pair would move, or a `DISTINCT` pair (another listing joining the same base is no conflict); when the two disagree the run says so in one Info line (`tobase.map: ticker.map decides N of its pair(s) otherwise`), naming each pair and the line that wins; a pair ticker.map pools the same way is silent. To keep a pair apart, write `DISTINCT US CA` in ticker.map; to pool it otherwise, your own `TOBASE` line. A line that cannot be used (not `TOBASE` / `DISTINCT`, a contradiction) stops the run like a ticker.map problem, named `tobase.map:N`. Without a ticker.map beside it, tobase.map is not read (a Warning; `update-tobase-map --write` creates an empty ticker.map).
- **`.V` and `.TO`:** in a Canadian project a TSX Venture listing and its TSX spelling are one listing for every `TOBASE` and `DISTINCT` line of ticker.map and tobase.map: `TOBASE ZZV.US ZZV.V` also books `ZZV.TO` as `ZZV.V`, `DISTINCT ZZW.US ZZW.V` also keeps `ZZW.TO` apart. The reverse needs nothing: Questrade, RBC, IB, Webull and a `.tt` line book a Venture line in Canadian dollars as `ROOT.TO`, so the books never hold `ROOT.V`. The master writes the `.TO` spelling. `GLOBAL` lines are matched as written.
- **Options:** as with any `TOBASE` line, the options of the listing a pair moves are booked under the base listing's code (`QZAB270115C00010000.US` as `QZA270115C00010000.TO`; the superficial-loss rule then sees a call as the shares' replacement), except where the account trades that code natively; `taxjson t1135` then reads them by the base listing's country (a `T1135` line in ticker.map sets another). This is taxjson's T1135 filing position (tax-logic CA-RPT-17): a US-listed option on a Canadian issuer pooled under its Canadian listing is not specified foreign property, since a right to acquire shares of a Canadian-resident corporation (which are not specified foreign property) is not caught by s.233.3(1); CRA's T1135 guidance does not address listed options.
- **Ended interlistings (`until`):** never deleted (the years they traded still pool them). A row in an ended listing dated after its `until` is a Warning naming the line: the ticker may now be another security's (after an acquisition a US ticker is often given to another company). If it is, write `DISTINCT US CA` in ticker.map. `until=unknown` gives no such Warning. When the master knows the ticker names another security today, the line carries `reused_by="<name>"` and **every** row of that listing is a Warning, whatever its date (a reused ticker in the books may be either company). The maintainer's build refuses an ended US ticker that names another security today unless the reuse is recorded.
- **The issuer's country (`country=`):** a pair booked under the US line of an issuer the master knows is domiciled outside Canada carries the issuer's country (ISO alpha-3, from its ISINs): `taxjson t1135` reads that symbol as that country — `BEP.US` (the TSX-held `BEP.UN` units booked under it) as Bermuda (`BMU`), not the `.US` suffix's USA. A `T1135 SYMBOL COUNTRY` line in ticker.map overrides it, and a `T1135` line naming a listing a `TOBASE` line books under another symbol applies to that symbol (the "matches no symbol" warning names the symbol it is booked as). Effects of the home listing on the books: a foreign issuer's TSX-held units (BEP.UN, BIP.UN, ...) are foreign property and pay foreign dividends; a Canadian issuer's NYSE- or Nasdaq-bought shares and their options, booked under the TSX line, are not foreign property (they leave the T1135) and pay Canadian dividends.
- **Retractions:** a listing the master shipped in error is retracted (recorded in the master with its reason; never added again). `update-tobase-map` removes its line when you have not edited it, and flags an edited one (ATTENTION, kept).
- **Not checked as unused:** `taxjson ticker-map --suggest` never lists a tobase.map line as an unused rule (most pairs are there for a security the books may hold one day), and never reads a tobase.map pair as a sighting of a cross-listing (a TSX Venture junior sharing a US company's letters is no pair to verify). It lists your own ticker.map `TOBASE` / `DISTINCT` lines that tobase.map states identically under "Covered by tobase.map (delete?)" (JSON `covered`): keeping one is harmless — it holds even if the master later retracts the pair; on a terminal `--write` asks keep / delete for each (keep is the default), `--all` never deletes one. `taxjson format-map` leaves tobase.map alone (it lays out ticker.map only; `update-tobase-map --write` lays tobase.map out).
- **Marked lines and your edits.** Each master line ends `# master:<FIGI>:<check>`, the check a short hash of its rule as written: a line whose rule no longer matches it (another target, the direction reversed) is your edit — kept as written, listed, and the master's line for the same listing is not added beside it (a marker without the check, from before v0.27.0, is your edit when the master could not have given its rule). `update-tobase-map` adds new interlistings and listings (the OTC ones too), annotates ended ones, removes an unedited marked line the master no longer gives or retracted, adds the new line of a US ticker change beside the old (when both already book under the same listing nothing more is needed; otherwise it names the dated `.tt` `RENAME <date> OLD NEW` event to write), and keeps your own unmarked lines, your comment lines (each with the line below it), your words after a marker, and any line that is not `TOBASE` / `DISTINCT` (listed: the run refuses it — it belongs in ticker.map).
- **Opting out of a master line:** delete it (or comment it out). The next `update-tobase-map --write` records a deleted line as `# removed: master:<FIGI> TOBASE A B` in a section of its own and never adds it back (a line of a listing new since your file's master is added, never taken for a deletion). To have it back, delete the `# removed:` line and copy the master's line back in (or write the pair in ticker.map). Its pairs that your ticker.map decides otherwise are reported, never changed. Without a tobase.map it lists the pairs that would change the books (a pair whose listing the books hold: both listings held — one pool; one — the rows booked under the Canadian listing) and writes nothing without `--write`.
- **Years:** with one shared file, `taxjson years` names it and the years reading it, and `taxjson years --diff A B` / `taxjson align` compare each year's `tobase_map` setting, never the file. With per-year copies (before v0.27.1) `taxjson years` counts each year's tobase.map lines that differ from the newest year's, `--diff` lists them, `align` never copies them, and both name `taxjson migrate`.
- **Checklist:** the `tobase-map` item reads the file the year reads (a shared one named `../tobase.map, shared by every year`): attention while it is older than the installed master (its `# master-generated` date) or still holds a `DISTINCT` line an earlier version wrote, a step to read when there is none.
- **Country:** Canada (`taxjson update-tobase-map` is refused in a US project). The US engine will read the same master as the substantially-identical groups of the wash-sale rule; until then a US project's listings are joined only by its own ticker.map lines (US-XLIST-05).
- **Code:** `src/taxjson/lib/tobase_map.py` — `compute_overlay`, `overlay_for`, `render`, `plan_update`, `until_findings`, `books_changes`, `receipt_pairs`, `plan_shared`, `apply_shared`; `src/taxjson/lib/project_layout.py` — `tobase_map_path`, `tobase_setting`, `tobase_layout`; `src/taxjson/bin/taxjson_ticker_map.py` — `_parse_map_file`; `src/taxjson/bin/taxjson_run.py` — `cmd_update_tobase_map`, `_tobase_root_year`, `_migrate_shared_tobase`, `_say_tobase_map`, `_say_tobase_until`; `scripts/build_interlisted.py` — `build`, `merge_previous`.

## `taxjson update-tobase-map --json`

A stable schema for programs (new keys may be added; none is renamed or removed while `schema_version` is 1). Code: `src/taxjson/bin/taxjson_run.py` — `cmd_update_tobase_map`.

| Key | Meaning |
| --- | --- |
| `schema_version`, `exists` | `1`; whether the project has a tobase.map |
| `master_generated` | the installed master's `generated` date |
| `written` | whether this call (`--write`) wrote the file |
| `ticker_map_decides` | `[{"line", "why"}]`: the master's pairs ticker.map decides otherwise (reported only) |

Without a tobase.map: `lines` (how many pair lines one would hold) and `changes_books` (`[{"line", "how"}]`: the pairs that change these books). With one: `file_master_generated` (its `# master-generated` date, or `null`), `added` (`[{"line", "figi", "section"}]`), `added_in_books` (the added lines naming a symbol of the books), `ended` (`[{"line", "until"}]`), `retracted` (`[{"line", "why"}]`: removed), `retracted_but_edited` (`[{"line", "why"}]`: kept, check them), `edited` (lines), `edited_replaces` (`[{"master", "yours"}]`), `unknown_figi`, `opted_out`, `removed_by_you` (lines), `not_tobase_lines` (kept as written), `made_ticker_map`, `ticker_changed` (`[{"old", "new", "until", "pooled"}]`; `pooled`: both already book as one symbol, nothing to write).

## .tt files

Hand-entered rows, any `*.tt` file in `inputs/<account>/` (TaxTrak format); `taxjson run` merges, sorts and de-duplicates them into the account. One space-separated line per row; `#` starts a comment; numbers take a decimal point (a thousands comma is fine, a decimal comma is refused); symbols are upper-cased and carry their listing suffix (`.TO`, `.US`; options in OCC form, `ZZQ271217C00036000.TO`) — write them as the account's exports spell them: a suffix that is not a market (`ZZQ.TSX`, `ZZQ.CA`) is a warning naming the line, since it would be an ACB pool of its own. The rows that take a time column need it (`09:30:00` is fine): the field positions depend on it. A line has one date, used as both trade and settle date: write the date that matches `tax_date` (the settlement date under `settle`). Rows at one date and time are taken in file order, as an export's rows are: write a same-day sale and rebuy in the order they happened. Validate a file with `taxjson-convert-tt --account margin inputs/margin/x.tt` (it prints the parsed rows and stops on a malformed line). The reverse, `taxjson-convert-tt book.json out.tt`, writes each row's settlement date (`--date-basis trade` for a trade-basis project) with its income facts and contract size, and counts the rows whose two dates differ; `taxjson events PERIOD ACCOUNT` prints the books' rows in this format to copy from. The parser is `src/taxjson/bin/taxjson_convert_tt.py` — `parse_tt_line`, `parse_opening_line`, `parse_inkind_line`, `parse_journal_line`, `parse_rename_line`, `expand_acquired`, `_VALID_ACTIONS`, `_SUGAR_ACTIONS`, `_EVENT_ACTIONS`. The dated events (`JOURNAL`, `RENAME`) are written date first with no time column; `taxjson run` books them (`src/taxjson/lib/dated_events.py` — `read_declarations`, `settle_journals`, `write_sidecars`) and records each in `work/dated_events.state` with its source (`tt`, `map`, `ib-conid`, `broker`) and place.

#### `BUYSELL`
- **Form:** `BUYSELL DATE TIME SYMBOL QTY CUR PRICE TOTAL [FEE] [xSIZE]`
- **Meaning:** a trade. QTY positive = buy, negative = sell; TOTAL is the net cash (your confirmation's net amount): buy = qty × price + fee, sell = qty × price − fee, written positive. A sale whose fee exceeds its gross (a penny option close) has a negative total, read when the line's fee explains it; any other negative sale total is refused, and a `0` there is warned about (it leaves the excess commission out of the loss). A total more than 1% (at least 0.05) off qty × price ± fee is booked as written and warned about on the run's console (file:line, both figures); `run --strict` stops on it and `taxjson checklist` lists it. A line with price 0 states its amount by the total alone and is not compared. `xSIZE`, last on the line, is a contract size other than an equity option's 100 (`x1000` for a CL futures option, `x50`, `x0.1`): the check then uses qty × price × size, and a futures line (`F:` or `/`) is checked only when it carries one. `taxjson find-missing-history --write-purchases` drafts these lines from a broker's cost into `inputs/<account>/purchases_draft.tt.txt` (never read until you rename it to end in `.tt`); a draft's `YYYY-MM-DD` date or `COST` total is a placeholder the reader refuses until you fill it in.
- **Example:** `BUYSELL 2025-03-10 09:30:00 ZZQ.US 10 USD 45.00 459.95 9.95`

#### `ASSIGN`
- **Form:** as BUYSELL.
- **Meaning:** the two legs of an exercise or assignment, one line each: the option leg at price 0 and the stock leg at the strike; the premium folds into the shares' cost or proceeds. A written call assigned on 1 contract:
- **Example:** `ASSIGN 2025-06-20 16:00:00 ZZQ250620C00005000.US 1 USD 0 0` then `ASSIGN 2025-06-20 16:00:01 ZZQ.US -100 USD 5 500`

#### `TRANSFER`
- **Form:** `TRANSFER DATE TIME SYMBOL QTY CUR PRICE TOTAL [DECLARED]`
- **Meaning:** shares moved in (positive QTY) or out (negative). `DECLARED` marks a deliberate statement (a custody-move counter-leg), which the transfer checks accept; it is opt-in, so an ordinary `.tt` TRANSFER line never gains that weight by accident. Two DECLARED lines of one symbol, `N` and `-N` at price 0 and total 0, declare broker churn that nets to zero (what `transfers_as_acquisitions = true` asks for; the run prints the exact lines).
- **Example:** `TRANSFER 2025-02-03 09:30:00 ZZQ.TO -20 CAD 30.00 600.00 DECLARED`; a zero-net pair: `TRANSFER 2025-04-22 09:30:00 ZZQ.TO 50 CAD 0.0 0.0 DECLARED` and the same line with `-50`

#### `ACQUIRED` (shorthand)
- **Form:** `ACQUIRED TRUE-DATE TIME SYMBOL QTY CUR PRICE TOTAL ARRIVED ARRIVAL-DATE`
- **Meaning:** shares that arrived by transfer, with their real purchase: it expands to a BUYSELL on the true date plus a DECLARED counter-TRANSFER on the arrival date.
- **Example:** `ACQUIRED 2019-05-10 09:30:00 ZZQ.TO 40 CAD 12.00 480.00 ARRIVED 2024-06-03`

#### `OPENING`
- **Form:** `OPENING SNAPSHOT-DATE SYMBOL QTY CUR TOTAL-COST [LOT-DATE]` (no time column)
- **Meaning:** an opening balance from a positions report: sets the position and its book cost (never its market value) on the snapshot day, is not a purchase (never a superficial-loss or wash-sale replacement, never a planning tool's recent buy), and replaces the account's earlier rows of that symbol (trades, transfers, renames and cost adjustments dated on or before the snapshot day; income rows stay; a left-out sale of the tax year stops the run). Long positions only: a written option or a futures contract is a BUYSELL line. Canada: a foreign-currency cost is converted at the snapshot day's rate. A US line is one lot: it needs its lot date and a USD cost. `taxjson opening` writes these lines.
- **Example:** `OPENING 2023-12-29 ZZB.TO 20 CAD 204.95`

#### `OPENING ... cost=unknown`
- **Form:** `OPENING DATE SYMBOL QTY cost=unknown [reason="TEXT"]` (no time column; the reason, in double quotes, is yours and may hold a `#`).
- **Meaning:** QTY units of SYMBOL held on DATE, bought before your files start at a cost they do not give — missing history, as a dated event. Sales that draw on them have an unknown cost: listed for manual reporting and left out of the totals (Canada: with no superficial-loss test, until the position is fully sold; US: FIFO, the opening lot first). The line's quantity and date are the opening, exactly: never sized from the rows, no tax-year window. Date it before the first sale it covers (the day before the account's first row is always early enough); a position already short before the date, or short again once the units are used up, is an ATTENTION line naming the line. A line naming a ticker's new symbol, dated before the change, opens the old symbol (the units the account held that day); one naming the old symbol, dated after the change, opens the new one only when the account folds the change's late rows (`late=fold`), as its rows are booked. The symbol goes through ticker.map and tobase.map as a row's does: the base-currency books open the TOBASE target, the native holdings view the listing the books trade; two lines that become one security open its units together when they have one date (on two dates the run stops). The units are held even when no later row trades them (a holding kept, or one that only pays income); when no row at all has the symbol a note says so (check the spelling). One line per symbol per account (two stop the run). In the shared layout (`inputs_dir`) the line is read by every year's project. `taxjson find-missing-history --write-missing-history` writes these lines into `inputs/<account>/missing_history.tt` — dated the day before the account's first row, the quantity the units the run opens — merged with the lines the account's .tt files have: never written twice; a symbol a line already opens with another quantity is a Warning and the line is left as it is. It writes only positions that go short through the tax year (`--all-history`: every year; `--outside-year`: only those with no row in the tax year), never $0-cost corporate-action shares (they need a merger or spin-off cost) and never a journal's own days. A line the detection would not propose (a short the broker marks as one: RBC `SHORT.`, IB code `O`) is still applied, with an ATTENTION line, and listed under REMOVE; a line whose position no longer goes short (its purchase is in the books now) is listed under STALE: delete it. It is never a row of the books: `taxjson run` and every command that applies missing history read it (`src/taxjson/lib/missing_history.py` — `read_tt_openings`, `load_missing_history`, `synthesize_openings`; the parser is `parse_unknown_opening_line`). Both countries (tax-logic `CA-ACB-11` / `US-BASIS-04`). This line is the only form of missing history: the project-root `missing_history.json` (and its older name `phantoms.json`) of taxjson before v0.27.0 is no longer read — a project that still has one is refused, naming `taxjson migrate`, which converts it: each entry becomes a line with the units that project's run opened for it (sized through its year's end, or its recorded `quantity`), dated the day before the account's first row; in a year folder of the shared layout it merges every year folder's file into one record (entries the years size the same are one line; entries they disagree on — another quantity, listed in only some years — are listed and written only with `taxjson migrate --write`, each as the newest year listing it decides it — an explicit "opens nothing" there writes no line, a year whose books have no row of it does not decide; entries that open nothing in any year are dropped, said), then renames each file `missing_history.json.migrated` (`--dry-run` shows the plan). Each line is dated from the books of the year that sized it; an entry no year's books can date stops the migration with nothing written or renamed. The converter is `plan_missing_history_migration`.
- **Example:** `OPENING 2021-12-31 ZZQ.US 10 cost=unknown reason="bought at the old broker in 2015"`

#### `INKIND`
- **Form:** `INKIND DATE SYMBOL QTY CUR PRICE [TOTAL] [plan=KIND]` (no time column), in a taxable account's folder; or `INKIND DATE SYMBOL QTY plan=own` (no value).
- **Meaning:** declares and values an in-kind move between this taxable account and a registered plan (an RRSP or TFSA contribution, a withdrawal; US: an IRA distribution). QTY is signed as the shares move in this account: negative = out to the plan, positive = back from it. PRICE is the value per share in CUR; give `0` and a TOTAL to state the whole value. The line names this account's transfer row of that security and quantity nearest DATE (within 10 days) and books it as in kind, dated DATE: with the plan's transfer row of the same quantity (it settles an ambiguous pair the run would not book on its own; `plan=` picks the plan's leg), else the plan's rows that add up to it (a delivery in parts), else as a move to a plan outside the project — `plan=` names that plan (`rrsp`, `tfsa`, `ira` ...). `plan=own` declares the row a move of your own: never in kind, and it no longer makes another pair ambiguous or a delivery in parts suspect. `plan=` needs a value; a value that is not a finite amount is refused. It is never a row of the books; a line matching no transfer row is listed in the run's in-kind warning. The parser is `parse_inkind_line`.
- **Example:** `INKIND 2025-03-14 ZZQ.TO -100 CAD 15.00` (100 shares contributed at 15.00 each); `INKIND 2025-03-14 ZZQ.TO -100 plan=own` (that transfer-out was a move between your own accounts)

#### `JOURNAL`
- **Form:** `JOURNAL DATE FROM TO QTY [separate]` (no time column)
- **Meaning:** QTY units moved from listing FROM to listing TO of one security inside this account (a Norbert's gambit's journal, a TSX line moved to its NYSE line). Booked as the move's two transfer legs, kept with the account's transfer evidence (`work/<account>_tt-journal_transfers.json`), never a purchase or a sale: the two listings are joined as one security (as a ticker.map `TOBASE` line would; the base-currency listing is kept) when something shows they are listings of one security — one root (`ZZG.TO`, `ZZG.U.TO`, `ZZG.US`; a class or unit designator is part of the root; a depositary receipt — written on a venue `src/taxjson/data/markets.toml` marks `receipts = true`, a CDR on Cboe Canada such as `ZZG.NE`, or named as a receipt in the exports, `[lists] receipt_words`: CDR, ADR — is its own security, never one root with the share) or names in the exports that agree as a broker journal's legs' names must, and no names of two different companies; otherwise the run stops, and a ticker.map `TOBASE FROM TO` line is the deliberate join (the line is then booked). The legs move units inside this account only (never paired with another account's transfer); the holdings view moves the units, the missing-history checks read the day as a journal. Both countries. When the broker's rows already hold both legs (the account's out-leg of FROM and in-leg of TO, the same quantity, within 5 business days) the line is a duplicate: an Info line, nothing booked twice; with one leg there, only the other leg is booked. A ticker.map rule naming either listing wins; a `DISTINCT` line keeps them apart (a Warning). Refused in a crypto account, for an option contract or a future (never journaled into shares), for a date in the future and for an implausible quantity (over a trillion units). Two identical lines (account, date, FROM, TO, quantity) are one journal, with a Warning: two journals of one size on one day are one line with the total. A line that moves MORE units than a journal the broker's rows already hold between the same two listings on the line's date stops the run: write only the units the rows lack. A line on another date is another journal, booked. One a business day from the broker's journal (or on its trades' day) moving more units is booked and said as a Warning (a likely restatement); a line ending `separate` is a journal of its own: booked in full, never settled against the broker's legs near it (not even one of the same size), never said as a restatement.
- **Example:** `JOURNAL 2025-03-05 ZZG.TO ZZG.U.TO 100`

#### `RENAME`
- **Form:** `RENAME DATE OLD NEW [late=fold|late=separate]` (no time column)
- **Meaning:** a ticker change on that date, booked as an event: the pool (US: the lots and holding periods) carries from OLD to NEW. Written in any account's .tt file, it applies to every account of the same kind (securities or crypto: a crypto account's line is a coin's ticker change) whose books hold OLD before the date — or a symbol an earlier change moved into OLD — and is recorded once. Every declaration of one change (`.tt` lines in several accounts, a legacy ticker.map line, within a week of each other) is one event dated the earliest (an Info line); the changes apply in date order. Refused, naming the lines: OLD renamed to another symbol within a week, one change on two dates further apart, a cycle (`A -> B` and `B -> A`), one account choosing both `late=` values. A trade in OLD after the change (later on its own day included) is listed by `taxjson renames` and stops `run --strict` until `late=fold` (book it as NEW) or `late=separate` (another company reusing the ticker). A line's `late=` applies to its own account's late rows and to every account without a line of its own that held OLD before the date; another account's own line may choose otherwise for its rows. An account that did not hold OLD before the date (another company may use the ticker there now) keeps its OLD rows as OLD, listed as undeclared (an Info line says so), until a line of its own says `late=`. An account's late rows are the ones its country's engine takes after its own rename row: in Canada (settlement-date order) every OLD trade executed on the rename row's date, even earlier that day than an evening corporate-action row; in the US (trade date and clock time) an OLD row before its broker's rename row is still OLD. A chain `A -> B` then `B -> C` declared for one day carries the position twice, in the order of the lines (a broker's or a `.tt` SPLIT into B that day counts too). OLD may be the symbol ticker.map gives the rows (`GLOBAL RAW OLD`): the line books on the RAW rows too. A line no account's books carry (no account of its kind holds OLD before the date: a typo of the symbol, a date after the last OLD row) books nothing: a Warning names the line, `taxjson renames` lists it under DECLARED, NOT BOOKED (`--pending` counts it), and `run --strict` stops. IB's one contract id under two symbols is booked as this event without a line (a Warning names the `DISTINCT` and `late=separate` way out) on the new symbol's earliest row of any section (a trade, a corporate action, a dividend, a return of capital) when every old-symbol row is on an earlier day; when the rows cannot date it (the two symbols' rows overlap, a symbol is listed under two contract ids in one statement) an ATTENTION line gives this line to add, with the date. A line for the change books it instead. A line dated in the future is refused, and so is one naming an option contract or a future: an option follows its underlying's ticker change (write the stock's line), and another expiry, strike or right is another contract. A line between two spellings of one listing in the books (`ZZA.TO` and `ZZA.CN`: a Canadian venue folds into `.TO`) books nothing, with an Info line.
- **Example:** `RENAME 2025-04-01 ZZOLD.US ZZNEW.US late=fold`

#### `ALLOWLOSS`
- **Form:** `ALLOWLOSS DATE SYMBOL [QTY] reason="TEXT"` (no time column; the reason is required, in double quotes, and may hold a `#`), in the folder of the TAXABLE account that sold.
- **Meaning:** a filing position against ONE superficial-loss denial (US: one wash-sale disallowance): that sale's loss stays allowed, and no ACB (US: basis) is raised for it on the replacement (no double benefit; US: no holding period carried either). DATE is the sale's trade or settlement date, SYMBOL its symbol as the books spell it (`taxjson wash-sales` shows it), QTY the units sold — needed only when two denied sales of the symbol share the day; it is the sale's total (one same-day sell-down in several fills is one sale, and one fill's units name no sale). A `reason=` after a `#` is part of the comment, not the line's reason. The replacement units the denial would use stay used, so every other sale's verdict is unchanged. It is never a row of the books: `taxjson run` reads every account's lines into `work/loss_overrides.json` and gives them to every engine run of the books (`--loss-overrides`). A line that names no denied sale, or two, stops the run naming it (with that day's trades in the account); a malformed line, a line in a sheltered account and two lines naming one sale are refused before the books are built (the same sale spelled twice — with and without the units, by trade and by settlement date — stops the run once the books name it). Every position is a Warning on every run and is listed in `taxjson sum` (FILING POSITIONS; `filing_positions` in `--json`, with the denial the rule would make and why), `taxjson wash-sales` (and each sale's `--explain` trace), `taxjson audit`, the US `taxjson wash-radar`, `taxjson carryover`, `taxjson handoff` and the checklist's filing-positions step, and noted on its row in `taxjson form-export` (Schedule 3 notes; Form 8949: no code W, nothing in (g), a note under the part and in the CSV's `note` column; a TXF export warns, its record carries no wash-sale amount). It is your position, not the rule's test (tax-logic CA-SL-18 / US-WASH-25): delete the line to apply the rule. The parser is `src/taxjson/lib/loss_overrides.py` — `parse_line`, `read_project`.
- **Example:** `ALLOWLOSS 2025-12-19 ZZQ.US reason="the RRSP call was bought 32 days after the trade date"`

#### `FXCONV`, `CASHMOVE`, `CASHOPEN`, `CASHBAL`, `CASHBOOK`
- **Form:** `FXCONV DATE FROM AMOUNT TO AMOUNT [value=BASE] [at=BOOK]`; `CASHMOVE DATE CUR SIGNED-AMOUNT HOW [at=BOOK]` with HOW one of `own`, `kept`, `spot`, `cost=BASE`, `proceeds=BASE`; `CASHOPEN DATE CUR SIGNED-UNITS BASE-COST [at=BOOK]`; `CASHBAL DATE CUR BALANCE [at=BOOK]` (no time column); `CASHBOOK BOOK [complete]` (no date), in the folder of a TAXABLE account.
- **Meaning:** cash events for the FX-on-cash ledger v2 (`fx_cash_ledger = "v2"`), never rows of the books; `taxjson run` only checks their form. `FXCONV`: a currency conversion no export carries (both amounts positive: what left, what arrived; `value=` the base-currency value when neither side is the base currency, else the day's rate prices it). `CASHMOVE`: cash in (+) or out (−) of an account. When a broker export carries the same move (same account folder, date, currency and amount) the line DECLARES what it was instead of adding one: `cost=` what money coming in from outside the books cost you (in the base currency), `spot` the day's rate, `own` a move between two of your accounts in the books (the units and their cost travel; the out must come before the in), `kept` money going out that you still hold in that currency elsewhere (it leaves at its cost, no gain), `proceeds=` what money going out fetched when converted or spent (a disposition). `CASHOPEN`: an account's SETTLED balance at the start of the year, dated Jan 1, with its cost (a negative balance is a debt; the cost is then what the borrowed units were worth when borrowed); `close-year` with ledger v2 records the next year's opening, so these lines are needed only for a first year. `CASHBAL`: the balance a statement shows at the end of that day (trade-date basis) — the ledger reconciles to it; an account with no statement balance (RBC, Webull, Coinbase, a bank) needs one on or after its last event of the year. `at=` names the account's cash book as `taxjson fx-cash --ledger v2` prints it (`ib`, `rbc:3f2a`, or any word for a book kept by hand such as `bank`); without it the line goes to the book the file's `CASHBOOK` line names, else to the folder's only broker account. `CASHBOOK`: whose cash the file's rows move — its trades, income and cash lines — in a folder holding several broker accounts (a Webull `.tt` beside IB and RBC exports: `CASHBOOK webull`); the ledger never guesses it from the file's name, and a file whose rows are several accounts' is split. `complete` after the book says its `.tt` cash lines are ALL of its conversions, deposits and withdrawals — needed for the book of a broker whose export the ledger reads none from (Webull, the generic importer) once it moves foreign cash. A malformed cash line stops `taxjson run` whichever ledger is in use; the message says it is read only by ledger v2. A row of a broker statement dated before the statement's period (a correction posted with the original date) moved its cash inside the period, and is walked at the period's first day. The parser is `src/taxjson/lib/cash_events.py` — `parse_line`, `FORMS`.
- **Example:** `CASHMOVE 2025-03-03 USD 5000 cost=6850.00` (a deposit the IB statement shows, declared); `CASHMOVE 2025-06-02 USD -2000 kept` (withdrawn to your own US-dollar bank account); `CASHOPEN 2025-01-01 USD 1200.00 1610.40 at=ib`; `CASHBAL 2025-12-31 USD 340.12 at=webull`; `CASHBOOK webull`; `CASHBOOK webull complete`

#### `SPLIT`
- **Form:** `SPLIT DATE TIME OLD NEW RATIO`
- **Meaning:** a split or consolidation (RATIO new shares per old: `2`, or `0.1` for one-for-ten) or, with RATIO `1` and a new symbol, a dated rename.
- **Example:** `SPLIT 2025-05-01 09:30:00 ZZOLD.US ZZNEW.US 1`

#### `ADJUST`
- **Form:** `ADJUST DATE TIME SYMBOL CUR AMOUNT [type=roc|dist] [record=YYYY-MM-DD]`
- **Meaning:** a cost adjustment, five fields after the action (not the BUYSELL shape): negative lowers the ACB/basis (a return of capital, T3 box 42), positive raises it (a stock dividend's declared amount, a notional distribution). On a short position it changes the cover's gain. `type=roc` lists it as a return of capital (`taxjson roc`, `taxjson roc-sum`); `record=` is the record date that dates a trust's return of capital. A positive ADJUST of a symbol in the account dated from 31 days before to 7 days after $0-cost shares arrived gives them a cost, and `taxjson find-missing-history` stops listing them. Enter a fund's return of capital as ADJUST lines or as `[[distributions]]`, never both.
- **Example:** `ADJUST 2025-12-31 16:00:00 ZZF.TO CAD -12.50 type=roc` (T3 box 42 return of capital)

#### `DISALLOW`
- **Form:** `ADJUST`'s shape.
- **Meaning:** a recorded loss disallowance, as `taxjson-convert-tt` writes it when converting a book that has one; rarely written by hand.

#### `DIVIDEND`, `DIVIDEND_IN_LIEU`, `TAX`
- **Form:** `ACTION DATE TIME SYMBOL QTY CUR PRICE GROSS [NET] [facts]`
- **Meaning:** income (or withholding, TAX). Facts are optional `key=value` tokens at the end: `record=` (the record date that dates trust income), `ex=`, `label=distribution`, `dealer=CA` / `issuer=CA` (a Canadian dealer's payment in lieu on a Canadian share is a deemed dividend in Canada).
- **Example:** `DIVIDEND 2025-01-06 09:30:00 ZZF.TO 100 CAD 0 25.00 record=2024-12-30 label=distribution`

#### `INTEREST`, `FEE`
- **Form:** `ACTION DATE TIME CUR AMOUNT`
- **Meaning:** income or a charge with no symbol (an interest figure a trade export lacks). A FEE charge is positive, a rebate negative.
- **Example:** `INTEREST 2025-12-31 16:00:00 USD 12.40`

### Duplicate rows across files

Overlapping exports of one account (a re-download, a 2025 export that runs into January beside the 2026 one) hold the same rows twice; the books keep each row once. The rule, shared by the books and the fees report (`src/taxjson/bin/taxjson_sort.py` — `DedupPlan`):

- every parser that sees the broker account (IB's statement account, Questrade's `Account #`, RBC's `Account`, Webull's preamble, a generic mapping's `account` column or `[broker] account`) stamps each row with it, hashed: rows of two different broker accounts are never one row;
- two identical rows in one file are one row, unless the parser marked them as separate fills (`[fill #2]`);
- the same row in two exports is one row when the files overlap as copies: on the dates both cover, one file's rows are a subset of the other's, and they share at least two rows;
- identical lines in two `.tt` files are two records, both booked, with an ATTENTION line (delete one if it is one trade typed twice); two identical `ACQUIRED` lots arriving the same day are two arrival legs;
- anything else (a `.tt` line equal to an exported row, files that share one row only or disagree on the dates both cover) is booked once, with a `Warning: Duplicates:` line naming both files — and, when they disagree, the rows only one holds (a newer statement that restated a row leaves the older version booked too: keep only the newer file; two real trades: enter the second as a `.tt` line);
- a `.tt` line that repeats an exported row by hand never has the row's id, so both are booked: the run warns when symbol, quantity, money and trade or settle date match — delete the line if it is that trade;
- one broker account's export placed under two `inputs/<account>/` folders is booked in both: an ATTENTION line names the two accounts, and `run --strict` stops.

---

## Generic importer mapping

For a broker taxjson has no parser for (how to set one up: [docs/brokers.md, "Any other broker (generic importer)"](brokers.md#any-other-broker-generic-importer)). Describe the CSV's layout in a TOML mapping: a sidecar `<file>.csv.toml` beside the export (any file name; it wins), or, for files named `generic_<anything>.csv`, one shared `generic.toml` in the same folder. A mapping is configuration: the file is read by the generic importer whatever its content (`src/taxjson/lib/brokerages/detect.py` — `generic_mapping`). A sidecar that is a dangling link is refused (the shared `generic.toml` is never used in its place). Unknown sections and keys are refused with a did-you-mean (`ammount`, `commission`, `[format]`). The CSV may be UTF-8 or UTF-16 (with its BOM); the mapping is UTF-8. Both countries. Parser: `src/taxjson/lib/brokerages/generic.py` — `_load_mapping`, `mapping_path`, `_COLUMN_KEYS`, `_FORMAT_KEYS`, `_DEFAULT_KEYS`, `_BROKER_KEYS`, `_OPTIONS`, `_VALID_TARGETS`. Template: `examples/generic_wealthsimple.toml`.

| Section | Keys | Meaning |
| --- | --- | --- |
| `[columns]` | `date` (required, the trade date), `settle`, `action`, `symbol`, `quantity`, `price`, `amount` (fee-inclusive signed total), `fee`, `currency`, `account` (the broker account of the row; wins over `[broker] account`) | CSV header names, case-insensitive; two fields may not share one header |
| `[formats]` | `date`, `settle` (strptime, default `%Y-%m-%d`; `settle` defaults to `date`'s format), `tax_sign` (`cash` or `withheld`), `fee_sign` (`cash`: a charge is negative cash, the default; `charged`: positive = charged) | how cells are written; quoted strings |
| `[actions]` | CSV value = `buy`, `sell`, `dividend`, `dividend_in_lieu` (a payment in lieu: ordinary income, never a dividend), `tax`, `interest`, `fee` or `skip` | the action map; unmapped values are counted, and one carrying a quantity or an amount is an UNBOOKED warning (refused by `run --strict`, failed by `taxjson-brokerage --lint`): map it, or map it to `skip` |
| `[defaults]` | `currency`, `action`, `symbol` | values for columns the CSV lacks (never a cell a cut-off file lost); quoted strings |
| `[options]` | `allow_large_fees` (a fee over 5% of the gross is refused unless true), `settle_on_trade_date` (the date column is already the settle date: every trade settles on it) | switches |
| `[broker]` | `name` (the real broker: letters, digits, `-`, `_`; lower-cased), `account` (the broker account every row belongs to, a quoted string; stored hashed) | labels. With a `name`, `taxjson run` parses that broker's files on their own (`work/<account>_generic-<name>.json`) and the fees report gives it its own row (`generic:<name>`); without one every generic file is one `generic` row. The account keeps identical rows of two broker accounts apart in the duplicate check |

```toml
[columns]
date = "Trade Date"
action = "Type"
symbol = "Ticker"
quantity = "Shares"
price = "Price"
amount = "Net Amount"
fee = "Commission"

[actions]
"BUY" = "buy"
"SELL" = "sell"
"DIV" = "dividend"

[defaults]
currency = "CAD"
```

**Money.** Signed amounts keep their sign (a negative dividend is a reversal). A `fee` row is booked positive = charged, like every broker parser: `fee_sign = "cash"` flips a CSV that shows a charge as negative cash, `"charged"` takes the cell as is. A buy or sell's commission keeps its direction: with an `amount` column the amount decides (a buy that cost less than qty × price was credited a rebate), else an explicit `fee_sign` does; a rebate is booked as a negative fee; with neither, the fee cell is a charge whatever its sign. With no `fee` column the commission is inferred as |amount| − qty × price (a commission-inclusive Net column). A futures symbol (`F:`, `/` or `\`, all spelled `F:`) needs the `amount` column: the contract size is never guessed.

**Cross-checks.** The import refuses rather than guess when:

- |amount| is not qty × price (× 100 for an OCC option symbol) ± fee within 1% (+0.05); an explicit 0 amount is checked too, only a blank cell derives it;
- a fee is above 5% of the gross without `allow_large_fees = true`; a row with no price is checked against its amount instead, and a fee of at least a buy's whole amount is refused;
- the currency is neither mapped nor set in `[defaults]` (no implicit USD), or a currency cell is not a currency code;
- a dividend, tax, interest or fee action has no `amount` mapped, or a blank amount cell;
- a buy or sell has no quantity, neither a price nor an amount, or is a stock buy at exactly zero cost;
- a sign contradicts the action: a negative quantity under `buy`, a positive quantity with a negative amount under `sell`, or a positive-quantity sell in a file whose other sells are negative (map buys and sells to separate values);
- a number has a decimal comma (`12,50`): only a thousands comma (`1,234`) is read;
- a record has more cells than the header or a cell holding a line break (an unescaped quote), fewer cells than the header, or the last record ends on a separator with no line break (a cut-off export);
- a `[defaults]` or `[formats]` value is not a quoted string.

It warns when the amount equals qty × price to the cent while the fee is not zero (the gross column is probably mapped as `amount`).

**Symbols.** Upper-cased (`sample` and `SAMPLE` are one security). A symbol written with a market suffix (`.TO`, `.US`, `.AX`, `.L`) keeps it: `ZZF.U.TO` bought in USD stays `ZZF.U.TO`. A Canadian venue (`.V`, `.VN`, `.CN`, `.NE`) is spelled `.TO`, as every broker parser spells it (`ZZA.V` becomes `ZZA.TO`). A bare symbol takes the suffix of the row's currency (`ZZA` in CAD is `ZZA.TO`, in USD `ZZA.US`).

**Dates.** Map `date` to the trade date. A settle column (`settle`, with `[formats] settle` when its format differs) is used when filled; otherwise a trade settles on the standard cycle of its listing's market — T+1 from May 2024, T+2 before, T+3 before 2017-09-05, options T+1, other markets on their own cycles (tax-logic CA-DATE-04 / US-DATE-04) — counted in that market's settlement days (the Canadian calendar for `.TO`, the US one for `.US`, the UK and ASX cycles for `.L` / `.AX`, else the row currency's), skipping weekends and holidays. With `tax_date = "settle"` a sale on Dec 31 therefore lands in January. A settle date before the trade date or more than 31 days after it is refused; more than 7 days after it is an ATTENTION line. Futures settle on the trade date (the next settlement day under `futures_settle = "next_day"`). An option closed at $0 on its expiry day is dated and settled that day (a $0 close posted up to 7 days later is moved back to it). Dividend, tax, interest and fee rows are dated `date`.

**Not covered.** There is no exercise or assignment target: a $0 option close beside a stock trade at the strike is an ATTENTION line — map those rows to `skip` and enter both legs as `.tt` ASSIGN lines. The importer books securities only (every symbol gets a listing suffix), so it is not a route for crypto: a generic file in a `crypto = true` account is refused; coins go in a crypto account (the Kraken or Coinbase export, or `.tt` lines with the bare coin symbol).

---

## holdings/: the year's positions snapshots

The broker's positions files for the tax year, as a download tool writes them (one `[[holding]]` TOML per broker account; see [Holdings TOML](#holdings-toml)). `taxjson sanity` with no arguments and the end of `taxjson run` compare the books with them, without a `holdings = [...]` setting; an account that has one keeps its own files (a file it lists is never another account's, nor named as unclaimed). Code: `src/taxjson/lib/holdings_dir.py` — `discover`, `folder_for`, `listed_files`.

- A file belongs to the account whose `account` or `broker_accounts` holds the file's `[meta] account`; else to the account its name starts with (`margin_holdings.toml`, `margin_ib_holdings.toml`). A file no account claims, or two do, is named in a note and not compared. Several files of one account are compared together.
- A snapshot is compared at its date: `[meta] as_of`, else the day of `[meta] generated_at`. One dated after the books' last day is compared with the latest books, with a note.
- Keep a snapshot taken at (or just after) the year end in that year's folder: it is the year's closing positions. A later download belongs in the later year's `holdings/`.

## Holdings TOML

A broker's positions on a date, read by `taxjson sanity` (and `[accounts.NAME] holdings`) and `taxjson opening`; sanity compares the quantities of every account and the costs of TAXABLE accounts only (a sheltered account's book cost — RRSP, TFSA, IRA, 401(k), HSA — is not a tax cost: one `Info:` line says so; `accounts_not_compared` in `--json`), and lists a dividend stating more shares than the books held on its record date (settled that day, or traded by the eve of the ex-date on the market's calendar; through a ticker change before the pay date) (else at any time in the 45 days to the pay date: `src/taxjson/lib/positions_check.py` — `income_share_mismatches`, `INCOME_WINDOW_DAYS`); `taxjson fetch --positions` writes one for Questrade. Never booked by `taxjson run`. Both countries. Reader: `src/taxjson/lib/positions_reports.py` — `_read_toml`.

| Key | Meaning |
| --- | --- |
| `[meta] as_of` | the positions' date (else the date of `generated_at`) |
| `[meta] account` / `broker_account` | the broker account (masked in output) |
| `[[holding]] symbol`, `quantity`, `currency` | required per position |
| `total_cost` (or `book_cost`, `cost_basis`) | the position's cost; else `average_entry_price` (or `cost_per_share`) × quantity |
| `cost_kind` | `average` (a Canadian book cost) or `lots` (a per-lot basis) |
| `acquired` | `YYYY-MM-DD`: this table is one lot bought that day (several tables of one symbol are several lots); a US opening needs it |
| `cost_currency`, `account`, `asset_type` (`cash` rows are skipped, `crypto`), `multiplier`, `market_value` | optional; market value is never a cost |

```toml
[meta]
as_of = "2024-12-31"

[[holding]]
symbol = "ZZQ.US"
quantity = 10
currency = "USD"
total_cost = 450.00
acquired = "2021-03-04"
```

---

## inputs/slips/: slips.toml and IB's dividends reports

What `taxjson slip-audit` compares with the books' income (Canada only; tax-logic `CA-SLIP-01` to `CA-SLIP-03`). Never booked by `taxjson run`: a slip changes no figure. Reader: `src/taxjson/lib/slip_audit.py` — `load_slips_file`, `ib_slips`, `find_ib_reports`; `src/taxjson/lib/ib_dividends.py` — `read_report`, `is_dividends_report`.

`slips.toml` holds the slips you type from the PDFs, one `[[slip]]` table per slip (`taxjson slip-audit --template` prints one with a T5 per account and currency that has income). Type each box as the slip prints it (a thousands comma is fine; IB's T3 prints `405 54` for 405.54: type `405.54`), and leave out the boxes it leaves blank. Never type a name, a SIN or an address.

| Key | Meaning |
| --- | --- |
| `year` | optional; must be the project's year (last year's file is refused) |
| `[[slip]] type` | `T5`, `T3` or `T5008` (an NR4 is refused: compare it by hand) |
| `account` | required: the taxjson account (`[accounts.NAME]`, taxable) the slip's payments are in |
| `issuer` | a label for the payer or broker (`RBC`, `IB`) |
| `currency` | the slip's currency (T5 box 27; RBC and Webull send one T5 per currency); default `CAD` |
| `broker_account` | optional: the account number on the slip, only to tell two broker accounts of one account label apart; it is hashed the way the books keep it and never printed (shown as its first two characters and `***`) |
| `broker_key` | what `--import-cra` writes instead: a 10-character key of the broker account the slip's payments match (a CRA copy shows no account number), salted with the project's own salt in `work/.slip_key_salt` so it cannot be turned back into the account number; the slip is shown with that account's input files. A key written with another salt (work/ deleted, another project) matches no account and is said: import the slip again. `taxjson redact` replaces it in its copy |
| `status` | `original` or `amended`, as `--import-cra` reads it from the slip line (a typed slip may leave it out); an amended slip replaces the original of the same issuer, account, fund and broker account |
| `source` | where the table came from: `cra:<file>` for an imported CRA slip (`cra:<file> #2` for the second slip of one PDF); IB's dividends report for the same broker account then keeps only its payments |
| `security` | a T3's fund (`XYZQ.TO`, or its root): its boxes split that fund's distribution |
| `code` | a T5008's type code (`SHS`, `OPC` ...): an aggregated T5008 is compared per class, as information |
| `boxes` | `{ BOX = AMOUNT }`. Compared: T5 24, 10, 18, 15, 16, 13, 14; T3 49, 23, 21, 24, 25, 26, 33, 34, 42; T5008 20, 21. Accepted and not compared: the derived boxes (T5 25, 26, 11, 12; T3 50, 51, 32, 39, 30 ...). Refused: the identifier boxes (recipient, account, report code) |
| `[[slip.line]] symbol`, `date`, `box`, `amount` | optional per-security lines (an RBC summary page, a split-corp page): a box-18 or box-42 line makes the suggestions name the security; `date` is optional |
| `[[ib_report]] file`, `account` | the account an IB dividends report belongs to, when no account's books carry its IB account |
| `[annual_average] CUR = RATE` | the year's average rate to use instead of the FX cache's (CRA's published one, say) |

```toml
year = 2025

[[slip]]
type = "T5"
issuer = "RBC"
account = "margin"
currency = "USD"
boxes = { 15 = 60.00, 16 = 9.00 }

[[slip]]
type = "T3"
issuer = "RBC"
account = "margin"
security = "ZZF.TO"
boxes = { 49 = 70.00, 50 = 96.60, 21 = 20.00, 42 = 10.00 }
```

**The slips CRA holds** (My Account › Tax information slips, one PDF per slip, printed from the browser): `taxjson slip-audit [ACCOUNT] --import-cra <folder or PDFs>` reads them with `pdftotext` (poppler-utils) — the slip line (year, T5 or T3, issuer) and the box rows only; the page's name, address and SIN are never read — and shows the `[[slip]]` tables it would add, each placed by the books: a T5 in the broker account of the issuer whose dividends, withholding and interest it matches (one broker's slips shared out among its accounts), a T3 in the fund (`security`) whose distributions match it. Each slip counts once: a PDF holding two slips is read as two; a slip read twice (a second download, a file named with its folder) or already in `slips.toml` with the same boxes is not added again; an amended slip replaces its original — in the same import, or a table already in `slips.toml`, which is commented out under "# Replaced by the amended slip …" (the file before is kept as `slips.toml.bak`) — and an original whose amended slip is there is not added. `--write` writes them to `slips.toml` (checked to read back first); `--json` prints the import as JSON. A T5's broker is the one whose whole name its issuer carries, or whose carrying dealer the issuer is: some brokers' slips are issued by the dealer that holds their accounts, named in the shipped `src/taxjson/data/slip_issuers.toml` (`[[alias]]`: `broker`, `issuer` — each language half's name, matched word for word in one half of CRA's "ENGLISH INC./FRENCH INC" with case, punctuation and the legal form ignored — and `source`). It lists "CI INVESTMENT SERVICES" for Webull Canada; another CI business (CI Direct Investing, CI Global Asset Management) is not matched. Such a slip is placed only in a broker account of that broker the books hold; a broker account whose export has no income rows (Webull's export books trades only) is one when none of that broker's accounts has payments in the books, and two such cannot be told apart. A slip it cannot place (a bank's T5, a broker whose whole name the issuer does not carry — "TD DIRECT INVESTING" is not RBC Direct Investing — a carrying dealer's slip in a project without that broker, or a USD slip with no rate in the FX cache) is listed: import it again with ACCOUNT to put it in that account. Other PDFs (an RRSP receipt, a T2202), a French page and a cancelled slip are skipped, said. Code: `src/taxjson/lib/cra_slips.py` — `parse_slips`, `read_pdf`, `broker_of_issuer`, `issuer_aliases`, `place`, `plan_import`, `table`.

IB's dividends report (Reports › Tax › Dividend report, saved as CSV: `U1234567.2025.dividends.csv`) is read as is, payment by payment: each payment's `RevenueComponent` rows give its slip boxes (`T5: Eligible Dividend Income` box 24, `T5: Capital Gains` box 18, `T3: Eligible Dividend Income` box 49, `T3: Foreign Non-Business Income` box 25, `T3: Return of Capital` box 42, `T3: Capital Gains` box 21, a plain `Ordinary Dividend` box 24 for a Canadian issuer and box 15 for a foreign one; `Withhold` box 16 / 34), at IB's rate (`GrossInBase`) — for an account whose base currency is not CAD, each payment at the Bank of Canada rate of its pay date instead (a Canadian slip is in CAD; tax-logic `CA-SLIP-02`). Its `Account` section names the holder: taxjson reads only the account number (hashed, masked when shown) and the base currency; a report of two accounts is refused (its payments do not say which account they are in), and two copies of one report are read once. The report is matched to the project account whose books carry that IB account; `reconcile-slips` and the checklist's T5008 step skip it. The report has no interest: type the T5's box 13 as a `[[slip]]` with the IB account as `broker_account` to compare it.

---

## inputs/ACCOUNT/manifest.json: corporate-action elections

Your tax election for each merger, spin-off or name change, keyed by event id; written by `taxjson run`'s prompt or `taxjson elect ACCOUNT --set EVENT_ID=ELECTION --hint KEY=VALUE`. Commit it. Reader: `src/taxjson/lib/corp_actions.py` — `Manifest`, `HINTS_BY_ELECTION`, `RULES_BY_COUNTRY`.

```json
{"elections": {"<event id>": {"summary": "...", "election": "rollover_s_86_1",
  "notes": "", "hints": {"allocated_acb_cad": 300.0}}}}
```

| Event | Canada elections (hints) | US elections (hints) |
| --- | --- | --- |
| merger | `taxable_disposition` (`fmv_per_share` when the broker booked $0), `rollover_s_85_1_5` | `taxable_exchange` (`fmv_per_share`), `reorg_368`, `reorg_368_boot` (`cash_boot`, `fmv_per_share`; an older manifest's `source_basis_total` is accepted and ignored) |
| spin-off | `taxable_deemed_dividend` (`fmv_per_share` when the broker gave no value), `rollover_s_86_1` (`allocated_acb_cad`, the CAD cost moved; file the s.86.1 election) | `taxable_distribution_301` (`fmv_per_share`), `tax_free_355` (`allocated_acb`, the USD basis moved per Form 8937) |
| name change | `rename` | `rename` |
| any | `ignore` (broker noise only; on a real event the books are wrong) | `ignore` |

Every hint is a non-negative amount. A run with `--no-input` leaves unresolved events in `work/pending_elections.json`; `taxjson elect --pending` shows them with ready-to-copy `--set` lines.

---

## inputs/CRYPTO ACCOUNT/sends.json and crypto_sends.tt

`sends.json` holds your decision for each crypto send that did not arrive in another of your crypto accounts; `taxjson crypto-sends --set ID=DECISION` writes it. Commit it. `crypto_sends.tt` is generated from it (with a shared `inputs_dir`, in the year's `work/crypto_sends/<account>/`: a run never writes the shared folder) (one sale at fair value per gift or payment, and per network fee): never edit it; a hand-written file of that name is never overwritten. Reader: `src/taxjson/lib/crypto_sends.py` — `load_decisions`, `record_decision`, `DECISIONS`, `REFUSED`.

```json
{"schema_version": 1, "sends": {
  "<send id>": {"decision": "gift", "note": "birthday", "price": 61.25},
  "<send id>-fee": {"decision": "fee", "price": 61.25}}}
```

| Key | Meaning |
| --- | --- |
| `decision` | `self` (your own wallet), `gift`, `payment`; a `-fee` id takes `fee` |
| `price` | fair value per coin in the base currency when the lookup fails (required for `fee`) |
| `note` | kept with the decision and written into the `.tt` |
| `unpair` | `true`: keep a send the tool paired out of the pairing (that arrival was unrelated) |
| `summary` | written by the tool |

Country: `gift` is Canada only (a disposition at fair value, s.69(1)(b)); a US project refuses it — record a gift as `self`.

---

## filed/YEAR.json: close-year locks

Written by `taxjson close-year`: the closed year's sales, year-end positions and cost, its country and date basis, and the carry-forwards (net capital loss or US short/long-term carryover; Canada's minimum tax carryover); with `fx_cash_ledger = "v2"` and a computed ledger, `fx_cash_v2` — each account's foreign cash and its cost at Dec 31, each debt — the next year's opening pool. Read by `taxjson check-filed`, `taxjson handoff`, `taxjson carryover`, `taxjson option-boundary` and the next year's estimate (through `prior_year_record`). Commit it; do not edit it. A lock closed under the other country's rules is refused.

---

## `taxjson years --json`

The year projects of a folder of exports shared by every year (`taxjson years` there or in a year folder): a stable schema for programs (new keys may be added; none is renamed or removed while `schema_version` is 1). Code: `src/taxjson/lib/project_layout.py` — `years_report`.

| Key | Meaning |
| --- | --- |
| `schema_version`, `root`, `newest` | `1`, the folder holding the year folders, the newest year |
| `years` | one object per year folder (`YYYY/` with a `taxjson.toml`), oldest first (below) |

Each year: `year`, `folder`; `filed` (its `filed/<year>.json` exists), `closed_at`, `partial_lock` (taken before the year ended), `totals` (`realized`, `disallowed`, `income` of the lock); `last_run` (the last full run, ISO), `stale` (`true` when the inputs or settings changed since; `null` when there is no run), `changed` (what changed); `differs_from_newest` (`{"map_rules", "tobase_rules", "keys"}`: how many ticker.map rules, tobase.map lines — per-year copies only; a shared file is compared as its `tobase_map` setting, one of the keys — and settings differ from the newest year's; `null` for the newest); `tobase_map` (the tobase.map the year reads, from the folder holding the years: `tobase.map` when shared, `2025/tobase.map` for a copy of its own, `null` for none) and `tobase_shared` (`true` when it is the shared one); `marks` (`{"done", "skipped"}` checklist marks); `problem` (a file that cannot be read, else `null`).

## `taxjson journals --json`

A stable schema for programs (a checklist, a script): new keys may be added, none is renamed or removed while `format` stays `taxjson-journals/1`. Read from the last run's `work/cross_listings.state`, the parsed exports in `work/` and the map the books were merged with; `taxjson journals` refuses a project with no completed run. Code: `src/taxjson/lib/journals.py` — `report`, `FORMAT`, `SOURCES`.

| Key | Meaning |
| --- | --- |
| `format` | `"taxjson-journals/1"` |
| `account`, `year` | the `--account` / `--year` filters, `null` when not given |
| `map` | the map the books were merged with: `work/ticker.map.effective`, `ticker.map`, or `null` |
| `journals` | one object per journal (below), ordered by account and date; with `--pending` only the pending ones |
| `counts` | `{"joined": N, "suggested": N, "refused": N, "decided": N}` over the journals `--account` / `--year` keep (`--pending` does not change them); `decided` counts the refused ones your ticker.map decided |
| `pending` | how many journals are pending (each journal's `pending`); `--pending` exits 1 when it is not 0 |

Each journal:

| Key | Meaning |
| --- | --- |
| `account`, `broker`, `date` | the out-leg's account, broker id (`rbc_direct`, `questrade`, `ib` ...) and date |
| `from`, `to`, `quantity` | the listing the units left, the listing they arrived on, how many |
| `in` | `{"account", "broker", "date"}` of the in-leg (another account or broker for a move across brokers) |
| `source`, `found_by` | how it was found: `questrade-brw`, `rbc-journal-ref` (a `J~` reference), `rbc-journal`, `ib-interdepot`, `cross-broker`, `transfer`, `tt`, `map`; a source another stage writes is passed through as it is. `found_by` is the words the text shows |
| `state` | `joined`, `suggested` or `refused` |
| `line`, `line_at` | joined: the map line(s) that pool the two listings and where (`ticker.map:N` for yours, `work/ticker.map.effective:N` for the run's own join); otherwise `null` |
| `reason` | suggested or refused: why not joined; `null` when joined |
| `settle` | suggested: the `.tt` line `JOURNAL <date> FROM TO QTY` and the ticker.map line, either of which settles it; `[]` otherwise |
| `undo` | joined or refused: how to undo or change the state (the `DISTINCT` line to add, the line to remove) |
| `pending` | `true` for a suggested journal and for one refused because its legs name two companies (no line of yours covers it); `false` when joined or when your ticker.map decided it (a `DISTINCT` line, a line naming a listing): a decision already made |
| `decided_by` | `"ticker.map"` when your map decided a refused journal, else `null` |
| `names` | the two legs' security names as the exports write them |
| `where` | where a declared journal was written (a `.tt` line's file and line), else `null` |

---

## `taxjson ticker-map --suggest --json`

The ticker.map lines the last run suggested, as one document (new keys may be added). Under `--write` the JSON is still the only thing on stdout: the plain lines (`Not added …`, `Nothing added to ticker.map.`, `Added N line(s) …`, the questions on a terminal) go to stderr, and `--write` with no terminal and no `--all` stops (exit 2) before any output while there is anything to choose — an evidence line or a pair to verify. Code: `src/taxjson/bin/taxjson_run.py` — `cmd_ticker_map`; `src/taxjson/lib/ticker_map_suggest.py` — `record`.

| Key | Meaning |
| --- | --- |
| `suggestions` | the lines the run's evidence names (`certainty` `"evidence"`): what `--write --all` appends; a script may append each one |
| `verify` | the listing pairs only you can answer (`kind` `"map-gap"`, `certainty` `"verify"`): `line` is the `TOBASE` (one security), `alternative` the `DISTINCT` (two); never written without your answer — keep them out of a script that appends `suggestions` |
| `skipped` | the lines left out, each with `why` and `by` (`"ticker.map"`: the map already answers it; `"suggestion"`: another suggestion covers it) |
| `unused` | the rename rules no symbol of the books reaches (`kind` `"unused-rule"`, `rule`, optional `hint`): to delete by hand, never written |
| `unused_unread`, `map_gap_unread` | the parsed sources the unused-rule and listing-pair checks could not read (their lists are then incomplete) |

Each suggestion: `line`, `reason`, `source`, `kind`, `certainty`, and when they apply `alternative`, `template` (`true`: a placeholder to edit, never written) and `tt` (`true`: a `.tt` line for an account's file, never written to ticker.map).

---

## `taxjson checklist --json`

The checklist as one document: a stable schema for programs (new keys,
statuses and items may be added; none is renamed or removed while
`schema_version` is 2). Inside a project the checks run the commands
that prove them; outside one nothing is evaluated.
Code: `src/taxjson/lib/checklist.py` — `to_json`, `guide_json`,
`SCHEMA_VERSION`, `STATUSES`.

| Key | Meaning |
| --- | --- |
| `schema_version` | `2` |
| `in_project` | `true` when the folder (or `-C DIR`) holds a taxjson.toml |
| `year`, `country` | the project's `[settings]`; `null` outside a project |
| `all_passed` | `true` when no step is open (the exit is then 0); `null` outside a project |
| `counts` | `{"done", "skipped", "attention", "todo", "blocked", "manual", "review", "n/a"}`: how many steps have each effective status; `null` outside a project |
| `next` | `{"step", "id", "title", "do"}` of the next step: the first one to do, needing attention or blocked that can be acted on now, else the first one to confirm (`do`: what to do, usually its first command); `null` when none, and outside a project |
| `steps` | one object per step, in order (below); with `--only ID`, that step only |

Each step:

| Key | Meaning |
| --- | --- |
| `step`, `id` | its number (1, 2 ...) and a stable id — the id `--done` / `--skip` / `--undo` take and `checklist.json` records (`install`, `inputs-frozen`, `run-clean`, `missing-history`, `t5008`, `filed-lock` ...) |
| `section`, `title` | the section it belongs to (`Set up`, `Get your files`, `Build the books`, `Fill the gaps`, `Tidy the config`, `Check`, `Results`, `Through the year`, `Share safely`, `Year end`) and what it is (a check's title in the project's country's words) |
| `check` | `true` for a check (a detector proves it), `false` for a step marked from the project's files |
| `command` | a check's proving command as one line (e.g. `taxjson find-missing-history`); a step's first command |
| `commands` | `[{"command", "note", "country"}]`: the lines to type, a short note, and `"canada"` / `"usa"` for a command only one country has (`null` for both); inside a project the other country's are left out |
| `how`, `why` | what to do besides the commands (may be empty), and why the step matters |
| `status` | what the detector or the step's rule found: `done`, `attention`, `todo`, `manual` (only you can confirm it), `blocked` (a prerequisite, usually `taxjson run`, is missing), `review` (yours to run and read: taxjson cannot tell whether you did) or `n/a`; `null` outside a project |
| `effective` | the status with your mark applied: `skipped` for a skip, `done` for a done mark (never over a finding: a done mark on an `attention` or `blocked` step leaves it so, unless the attention was a question the mark answered) |
| `detail` | what the check or the project's files say about it (empty when nothing to say) |
| `override`, `note`, `finding` | your mark (`done`, `skipped` or `null`), its note, and the detector's finding when a mark sits beside one |
| `next` | `true` for the step `next` names |

---

## `taxjson slip-audit --json`

The slip audit as one document: a stable schema for programs (new keys may be added; none is renamed or removed while `schema_version` is 1). Code: `src/taxjson/lib/slip_audit.py` — `audit`, `SCHEMA_VERSION`, `CATEGORIES`, `question_keys`.

| Key | Meaning |
| --- | --- |
| `schema_version`, `year`, `country` | `1`, the tax year, `"canada"` |
| `tolerance`, `fx_band` | the allowed difference per box (CAD), and the share of a box's converted money also allowed (`0.005`) |
| `sources` | the slip sources read: `{"kind": "slips.toml", "file", "slips"}` or `{"kind": "ib-dividends", "file", "account", "broker_account" (masked), "payments", "unknown_components", "problems"}`, with `"converted_from"` (the report's base currency) when its payments were converted to CAD at the Bank of Canada's daily rates |
| `annual_average` | `{CUR: {"rate", "observations", "source"} \| null}` for each foreign currency in sight |
| `accounts` | `[{"account", "status", "groups"}]`; a group is the slips of one broker account (or of the whole account): `{"broker_account", "slips", "buckets", "payments", "securities", "record_year"}` |
| `buckets[]` | per slip currency: `{"currency", "slips", "split_by_t3", "lines"}`; each line `{"category", "label", "boxes", "slip", "books", "diff", "tolerance", "status" (ok, differs, under-50), "converted", "books_annual_average", "closest" (daily, annual or null)}`, and in a foreign currency `"books_cad_daily"`, `"slip_cad_annual_average"` |
| `payments` | IB's payments against the books: `matched`, `missing_from_books`, `missing_from_slip`, `amount_differs`, `other_year` |
| `coverage` | `no_slip` (an account's income with no slip; `small_interest_only` when it is interest under 50) and `slips_without_books` |
| `t5008` | aggregated T5008 slips against the books' dispositions, per class (information) |
| `suggestions` | `capital_gains_dividends` (`{"symbol", "date", "year", "amount", "account", "toml"}`), `tt_lines` (`{"account", "file", "lines"}`), `notes` |
| `problems` | inputs not compared (an IB report no account carries, a second copy of a report, an unknown component, a `broker_key` no broker account matches) |
| `issues`, `status` | the findings `{"account", "kind", "text"}` (kinds: `differs`, `no-slip`, `no-books`, `empty-slip`, `missing-from-books`, `missing-from-slip`, `amount-differs`) and `ok` / `attention` / `no-slips`; the command exits 1 when `issues` is not empty |

`taxjson slip-audit --import-cra … --json` prints the import instead (`schema_version` 1): `year`, `slips` (each slip read: `file`, `type`, `status`, `issuer`, `currency`, `boxes`, `account`, `broker_account` (its input files), `fund`, `how`), `add` (the files whose tables are added), `replace` (`{"table", "by"}`: a table in slips.toml an amended slip replaces), `replaced_in_import`, `not_imported`, `tables` (the TOML text), `written`, `backup`. Code: `src/taxjson/bin/taxjson_run.py` — `_slip_audit_import_cra`.

---

## Old files and environment variables

- Old per-purpose files are no longer read and stop every command until `taxjson migrate` folds them in: `yf_ticker.map` (QUOTE lines), `crypto_ticker.map` (CRYPTO), `ticker_extraction_overrides.txt` (EXTRACT), `t1135.map` (T1135), `amt_carryover.txt` (`[estimate] amt_carryover`), `claimed_losses.txt` (`[carryover] claimed`), `capital_gains_dividends.map` (`[[capital_gains_dividends]]`), `distributions.map` (`[[distributions]]`). `tv_exchange.map` is only renamed. Code: `src/taxjson/lib/migrate.py` — `legacy_files`.
- `TAXJSON_LOCAL_TZ`: the crypto time zone outside a project (the setting wins).
- `TAXJSON_TICKER_MAP`: set by `taxjson` for every stage; the ticker.map a stand-alone tool reads.
- `TAXJSON_PROJECT_ROOT`: set by `taxjson` for every stage; the project an input file in its (possibly shared, `inputs_dir`) inputs folder belongs to — the IB parse reads that project's `.tt` RENAME lines and ticker.map. Code: `src/taxjson/lib/project_layout.py` — `project_of_input`.
- `TAXJSON_WIDTH`: the column width text wraps at, for a person. Unset: the terminal's full width (at most 160, never under 40), or 120 when the output is piped or redirected; `0`: no wrapping (what the run writes to `work/` and `reports/` is always unwrapped). Code: `src/taxjson/lib/out.py` — `width`.
