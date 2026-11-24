# Settings and project files

Every key `taxjson.toml` accepts and every other file a project reads, with its meaning, default, country, when to change it and an example. All examples use made-up symbols and amounts.

A project is a folder:

| Path | What it is | Section |
| --- | --- | --- |
| `taxjson.toml` | the configuration | [taxjson.toml](#taxjsontoml) |
| `ticker.map` | symbol rules and lookups (optional) | [ticker.map](#tickermap) |
| `tobase.map` | Canada: the interlisted master's pairs (a TSX share and its US listings: one security), kept by `tjs update-tobase-map` | [tobase.map](#tobasemap) |
| `inputs/<account>/*.csv` | broker exports, detected by content. Only `.csv` and `.tt` files are read: an Excel export (`.xlsx`, `.xls`) stops `tjs run` unless its CSV conversion (`taxjson-xlsx-to-csv FILE.xlsx -o FILE.csv`) sits beside it; a positions-only file (an RBC Holdings Export) is skipped with a note | — |
| `inputs/<account>/*.tt` | hand-entered rows | [.tt files](#tt-files) |
| `inputs/<account>/<file>.csv` + mapping | any other broker's CSV | [Generic importer mapping](#generic-importer-mapping) |
| `inputs/<account>/manifest.json` | your corporate-action elections (keep it) | [manifest.json](#inputsaccountmanifestjson-corporate-action-elections) |
| `inputs/<crypto account>/sends.json`, `crypto_sends.tt` | your crypto-send decisions (keep it) and the sales generated from them (never edit the generated file) | [sends.json](#inputscrypto-accountsendsjson-and-crypto_sendstt) |
| `inputs/<account>/missing_history.tt` | units held before your files start, cost unknown: `OPENING <date> <SYMBOL> <qty> cost=unknown` lines | [OPENING ... cost=unknown](#opening--costunknown) |
| `missing_history.json` (project root) | no longer read (taxjson before v0.27.0; `phantoms.json` before that): every command stops, naming `tjs migrate`, which converts it into `missing_history.tt` lines | [OPENING ... cost=unknown](#opening--costunknown) |
| holdings TOML (anywhere) | a broker's positions, for `tjs sanity` / `tjs opening` | [Holdings TOML](#holdings-toml) |
| `inputs/slips/slips.toml`, `inputs/slips/U*.YYYY.dividends.csv` | your T5 / T3 slips and IB's dividends reports, for `tjs slip-audit` (the project's own, also when `inputs_dir` names a shared folder). Not an account folder: it needs no `[accounts.slips]` | [inputs/slips/](#inputsslips-slipstoml-and-ibs-dividends-reports) |
| `inputs/slips/*.csv` | T5008 / 1099-B slips, for `tjs reconcile-slips` | — |
| `holdings/*.toml` | the year's broker positions snapshots (or the broker's RBC Holdings Export or IB statement, `.csv`), read by `tjs sanity` with no setting. Optional: with no folder or no file nothing is compared | [holdings/](#holdings-the-years-positions-snapshots) |
| `filed/<year>.json` | close-year locks (keep them) | [filed/YEAR.json](#filedyearjson-close-year-locks) |
| `snapshot/` | the copies `tjs snapshot` makes (`<year>.<timestamp>/`), beside the year folders in a multi-year project; never read by a run. `tjs init`'s `.gitignore` lists it; they cannot be rebuilt, so keep them in a backup if you want them | — |
| `work/`, `reports/` | rebuilt by every `tjs run` (`work/`: per-stage files and the price and FX caches; `reports/`: `<account>.sum`, `wash_radar_*`, `fees.rpt`, holdings); never edited. `tjs init`'s `.gitignore` lists them. `tjs clean` removes them, keeping in `work/` what no run rebuilds (docs/commands.md, tjs clean) | — |

Every map file and folder other than `inputs/` is optional. What cannot be rebuilt — `taxjson.toml`, `ticker.map`, `tobase.map`, `inputs/` (its `.tt` files, `manifest.json` and `sends.json` included) and `filed/` — is what to keep in version control or a backup.

The exports can live in a folder every year's project shares: `tjs init` makes that layout by default — `inputs/<account>/` beside the year folders (`2025/`), each a complete project whose `taxjson.toml` names the shared folder with `inputs_dir = "../inputs"` (see [Folders](#folders); docs/getting-started.md, "One folder of exports for every year"). Each year still has its own `taxjson.toml`, `ticker.map` and results; the exports are shared — with them the `.tt` files, so a `missing_history.tt` line is one record for every year — and, in Canada, `tobase.map` (the interlisted pairs: one file beside the year folders that every year reads, `tobase_map = "../tobase.map"`; each year's ticker.map still wins over it).

Rules every reader applies:

- `tjs init` writes every key of the project's country, the optional ones commented out with their default; `tjs format` puts an edited or older file back into that layout without changing what it configures, so two years' files differ only where their values do. The layout (`src/taxjson/lib/config_template.py` — `SETTINGS_GROUPS`, `TABLES`, `PROSE`): `[settings]` first, in the groups below, each under a `## --- Name ---` line with a blank line between groups; then the accounts (a commented reference block naming every account key once, then your `[accounts.NAME]` tables, key lines only, in your order); then `[estimate]`, `[carryover]`, `[[distributions]]` and, in Canada, `[[capital_gains_dividends]]` and `[instalments]` (`[[...]]` entries keep their order). Keys are alphabetical within a group or table (an account's `type` first), set and commented-out keys in one sequence, the `=` signs in one column per table. A description sits on the line(s) above its key; a key's list of values ends its line. Prose comment lines start with `## `; a commented-out key, table or line of a multi-line value starts with a single `# ` and is switched on by deleting it. `tjs format` sorts your own comment lines the same way: one that reads as TOML keeps `# `, any other becomes `## text`.
- An unknown key warns with a did-you-mean. A key or table the project's country does not own is refused, naming it (Canadian and US settings never mix; the owners are `src/taxjson/lib/country.py` — `SETTING_COUNTRY`, `CONFIG_COUNTRY`).
- Booleans are unquoted: `crypto = "false"` is refused, because a quoted string would read as true.
- Every key below is in the validator's lists: `src/taxjson/lib/config_check.py` — `ACCOUNT_KEYS`, `ESTIMATE_KEYS`, `INSTALMENTS_KEYS`; `src/taxjson/lib/project_tables.py` — `CARRYOVER_KEYS`, `DISTRIBUTION_KEYS`; `src/taxjson/lib/cg_dividends.py` — `CGD_KEYS`; `src/taxjson/lib/config_template.py` — `validator_keys`, `SETTINGS_SPEC`, `ACCOUNT_SPEC`, `TABLES`, `SETTINGS_GROUPS`. `tests/test_settings_doc.py` fails when one is missing here.

---

## taxjson.toml

### `[settings]`

The keys come in groups, in the order `tjs init` writes them.

#### Project

#### `year`
- **Meaning:** the tax year the pipeline reports on.
- **Default:** none — required. An integer from 1900 to next year.
- **Country:** both.
- **Change it when:** you start next year's project (a new folder per year is the usual layout; `tjs handoff` checks the hand-over).
- **Example:** `year = 2025`

#### `country`
- **Meaning:** whose tax law the project follows: `canada` (or `ca`) or `usa` (or `us`). taxjson never assumes one.
- **Default:** none — required by every command.
- **Country:** both.
- **Change it when:** never in a project with books; changing it makes every report refuse the old books until `tjs run` rebuilds them.
- **Example:** `country = "canada"`

#### `province`
- **Meaning:** the province `tjs estimate` taxes at: `ON`, `BC` or `AB`. Quebec and the other provinces are refused.
- **Default:** none. Without it `tjs close-year` uses a federal-only estimate and says so.
- **Country:** Canada only.
- **Change it when:** you want provincial tax and AMT in the estimate.
- **Example:** `province = "ON"`

#### `tax_date`
- **Meaning:** which date puts a trade in a tax year: `settle` or `trade`.
- **Default:** `settle` in Canada (CRA dates a disposition by settlement), `trade` in the US.
- **Country:** both.
- **Change it when:** almost never. Only to reproduce books filed on the other basis; `tjs tax-logic` states the rule in force. `.tt` lines carry one date: write the date that matches this setting.
- **Example:** `tax_date = "settle"`

#### `local_timezone`
- **Meaning:** the IANA zone crypto exchange rows (stamped in UTC) are dated in: in `America/Toronto` a Kraken or Coinbase fill at 03:00 UTC on January 1 belongs to the previous tax year. In the hour the clocks repeat when they fall back, the rows' times are compressed into that hour in the order they happened (01:50 EDT is shown 01:25, 01:10 EST after it 01:35), so a sale and a rebuy across the change stay in order. Outside a project the `TAXJSON_LOCAL_TZ` environment variable is used; in a project the setting wins.
- **Default:** none. A project with a `crypto = true` account stops every command until it is set (only `tjs format`, `format-map`, `migrate`, `init` and `help` still run), naming the key and suggesting this machine's zone; with no coins, delete the scaffold's crypto account instead. `tjs init` writes this machine's zone when it can read one (not a server's UTC).
- **Country:** both.
- **Change it when:** you set up a crypto account, or you moved. A change re-dates every crypto row and re-keys the crypto sends.
- **Example:** `local_timezone = "America/Toronto"`

#### `prior_year_record`
- **Meaning:** the path to last year's close-year lock, read by `tjs handoff`, `tjs carryover`, `tjs option-boundary`, `tjs edge-cases` and the carry-forwards of the estimate.
- **Default:** none (then `filed/<year-1>.json` in this project, if any). `tjs new-year` sets it to the previous year folder's lock.
- **Country:** both.
- **Change it when:** each year's project lives in its own folder.
- **Example:** `prior_year_record = "../2024/filed/2024.json"`

#### `requires_taxjson`
- **Meaning:** the oldest taxjson this project runs on, `">=X.Y.Z"`: the release that first reads the layout and files the project uses. Every command of an older taxjson stops before reading anything else, with one line: "this project needs taxjson X.Y.Z or newer (installed: A.B.C)" (exit 2; docs/troubleshooting.md). In the folder holding the year folders, and for `years`, `new-year`, `migrate`, `update-tobase-map` and `align`, every year folder's value counts. Releases before 0.28.2 do not read the key.
- **Default:** none (any taxjson runs the project). `tjs init` writes the release its layout needs: `">=0.27.1"` for a Canadian project of year folders (one shared tobase.map), `">=0.27.0"` otherwise (missing history as `.tt` `OPENING … cost=unknown` lines; tobase.map beside ticker.map).
- **Country:** both.
- **Change it when:** never by hand. `init`, `new-year` (last year's value, raised for its layout), `migrate` (the layout it migrated to: shared exports 0.26.0, `.tt` missing history 0.27.0, one shared tobase.map 0.27.1), `update-tobase-map --write`, `find-missing-history --write-missing-history` and `align --write` raise it; none lowers it. The release that reads each layout feature is listed once (`src/taxjson/lib/requires.py` — `FEATURES`, `raise_requirement`).
- **Rules:** exactly `">=X.Y.Z"`; anything else stops every command, naming the key.
- **Example:** `requires_taxjson = ">=0.27.1"`

#### Folders

Where the project reads its exports and positions and writes files for other tools. A relative path is read from the project folder; every one must stay inside the folder that holds the project (its parent) — a path or symlink leading further out is refused, naming it (`src/taxjson/lib/project_layout.py` — `folder_setting`, `setting_problems`).

#### `inputs_dir`
- **Meaning:** the folder of the broker exports (`<folder>/<account>/`: CSVs, `.tt` files, `manifest.json`, `sends.json`), when the year projects share one. The run reads it like `inputs/`; a folder of an account this year's `taxjson.toml` does not have (one split later, one closed earlier) is not read, with one `Info:` line. With it, a year folder named `YYYY` must hold that year's project (`year`), and the year's slips stay in the project's own `inputs/slips/` (slips belong to one year; any other folder there is not read, with a warning). `tjs fetch` downloads into it too, with a note that the download applies to every year.
- **Default:** `inputs/` in the project.
- **Country:** both.
- **Change it when:** `tjs init` sets it (`"../inputs"`); `tjs migrate --to-years` sets it on an existing project. `tjs close-year` comments it out (under a `## frozen by` note) after copying the files the filed books were built from into the project's own `inputs/`, so the filed year keeps reading that copy (not for a year locked before it ended, `--force`: that one keeps reading the shared folder); `tjs new-year` sets it again for the next year.
- **Example:** `inputs_dir = "../inputs"`

#### `holdings_dir`
- **Meaning:** the folder of the year's broker positions snapshots `tjs sanity` compares the books with, with no `holdings = [...]` setting (`tjs run` compares nothing: a snapshot there makes its closing list name `tjs sanity`) (see [holdings/](#holdings-the-years-positions-snapshots)). `tjs fetch --positions` writes `<account>_live_holdings.toml` there.
- **Default:** `holdings/` in the project.
- **Country:** both.
- **Change it when:** your download tool writes the snapshots elsewhere inside the folder holding the project.
- **New year:** a folder inside the year's own project, however it is written (`"snapshots"`, `"../2025/snapshots"` in 2025/), is the year's own: `tjs new-year` and `tjs align --write` write it for the other year as the same folder inside that year (`"snapshots"`), never a path into another year's folder, which the run refuses (`src/taxjson/lib/project_layout.py` — `own_folder_value`). A folder that is inside the year only by a link leading out of it (`2025/snapshots` a link to `../snapshots_all`, named `"../2025/snapshots"`) is shared, not the year's own: the value is kept. `tjs new-year` makes the new year's own folder empty and names it in its next steps; a shared folder it only names.
- **Example:** `holdings_dir = "holdings"`

#### `exports_dir`
- **Meaning:** where the newest year's full run copies its `reports/<account>_holdings.toml` (positions at the account's own cost, before the passes across accounts: `tjs list` has the filing cost) and wash radar (`wash_radar_*.json` and `.rpt`), with a `README.txt` naming the year, for other tools to read whatever the year. An older year's run leaves it alone ("newest": no year folder `YYYY` beside this one has a later year).
- **Default:** none: nothing is copied.
- **Country:** both.
- **Change it when:** `tjs init` sets it (`"../exports"`).
- **Rules:** a folder of its own: one that is, holds or sits inside the inputs folder (shared or the project's `inputs/`), the holdings folder, a year folder, the project's `work/`, `reports/` or `filed/`, or the project folder is refused, naming it (`src/taxjson/lib/project_layout.py` — `exports_overlap`). Each export records the files it wrote in `.taxjson-exports.json` there, and the next removes only those it no longer writes: a file you put in the folder is never touched.
- **Example:** `exports_dir = "../exports"`

#### `tobase_map`
- **Meaning:** the [tobase.map](#tobasemap) every year of a multi-year project reads: one file beside the year folders, so the interlisted pairs are the same for every year and `tjs update-tobase-map` (in any year folder, or in the folder holding them) updates them once. Each year's ticker.map still wins over it. A change to it is a change to every year's inputs: each year's run banner, `tjs years` and the checklist name it (`changed: ../tobase.map (shared by every year)`). A filed year closed with its inputs frozen reads its own copy instead (`tjs close-year`), and `tjs align` never points it back at this file.
- **Default:** `tobase.map` beside ticker.map (a single-folder project's root).
- **Country:** Canada (a US project reads no tobase.map: tax-logic US-XLIST-05).
- **Change it when:** `tjs init` sets it in the year layout (`"../tobase.map"`), `tjs new-year` keeps it, `tjs migrate --to-years` sets it, and `tjs migrate` sets it in each year folder when it makes the years' own copies (the layout before v0.27.1) one file. `tjs close-year` comments it out after copying the shared file beside `ticker.map` (the filed year reads its own copy); `tjs new-year` sets it again for the next year.
- **Rules:** like the folder settings, the path must resolve (a symlink followed) inside the folder that holds the project, and it must not name a folder, a file inside this project or one inside another year's folder (`src/taxjson/lib/project_layout.py` — `tobase_setting`, `tobase_map_path`). A year folder that has the setting and still holds a `tobase.map` of its own is refused, naming both (`tobase_both_problem`): `tjs migrate` makes them one.
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
- **Meaning:** `true` prints the FX-on-foreign-cash report at the end of `tjs run` (Canada s.39(1.1) with its $200 exemption; US §988, ordinary income) and writes `reports/fx_cash.rpt`. `tjs fx-cash` prints it on demand either way. With the default ledger the note says NOT RELIABLE, never a reportable figure (see `fx_cash_ledger`).
- **Default:** `false`.
- **Country:** both.
- **Change it when:** you hold material foreign cash and want the figure every run.
- **Example:** `fx_cash_gains = true`

#### `fx_cash_ledger`
- **Meaning:** which FX-on-cash ledger `tjs fx-cash`, `tjs sum`, the checklist and `close-year` use. `v1` reads only the trades and income of the taxable books (no conversion, deposit, withdrawal, margin balance or opening pool), so every output says NOT RELIABLE and shows no reportable figure. `v2` (opt-in, under audit) also reads the cash events — IB Forex trades, Deposits & Withdrawals and Cash Report balances; RBC cash rows; Questrade FX conversions (FXT), deposits, withdrawals, cash-only transfers and stock-lending income (LFJ); Kraken fiat funding, fiat conversions and ledger balances (the balance after the latest row; the rows of one second in the order the `balance` column gives when every row of the wallet chains from the wallet's balance before that second or to its balance after it, or, with no balance on either side, among themselves, to one end balance, and that chain is the order the export lists the rows in or is held by the balance on both sides; else in the file's order, which is the export's lines read from the last up where it lists the wallet's rows newest first (told from every pair of the wallet's rows that follow each other, so an export sorted by asset is read each wallet its own way; rows of one second that each state a fraction of it are listed in the order of those), the balance's `where` then saying which order was read and what settles it (a reconcile gap quotes it, and a Warning says it when the balance reconciles; so does a balance after a second of Hybrid Earn rows whose order is not known, since what is parked depends on it): rows that chain in another order than the export lists them in, with a balance on one side only, may lack a row among them or before them; an export whose rows of a wallet are in no one order of time (two exports pasted out of order, a row appended out of its place) or share one time states no direction: its seconds of several rows that the balances on both sides do not hold are read as their balances chain, and said, and sorting the export's rows by time, oldest first or newest first throughout, settles it; a Hybrid Earn move, `hybridearnwithdrawal` or `hybridearndeposit`, stays the account's cash: no move in either direction, listed as `PARKED` by `--cash-events`, what a wallet's rows parked added to that wallet's ledger balance, and only what comes back beyond what was parked a move to declare; a `.tt` `CASHMOVE` line written for such a row declares cash that never moved: one dated in the year computed is refused, named with its file and line, until it is removed, and one of an earlier year is a Warning saying that removing it changes that year; a line that fits both such a row and a real move of the same day and amount declares neither and is refused with every line alike and both rows named, until the line for the real move names its row with `row=`); Coinbase fiat and stablecoin moves and conversions; the decision `tjs crypto-sends` saved for a stablecoin send (inputs/<account>/sends.json: a gift or a payment is out at the day's rate, `spot`; a `self` send is `kept`; a `.tt` `CASHMOVE` line for the send is used instead when it says the same kind of thing, and the ledger gives no figure, naming both, when the line says still yours and the decision a disposition, or the reverse); and the `.tt` lines `FXCONV`, `CASHMOVE`, `CASHOPEN`, `CASHBAL` — models a negative broker balance as a debt in that currency, opens the year with the pool `close-year` recorded for the year before (or `CASHOPEN` lines), reconciles each broker account to its statement balances (tolerance 1.00 unit), and when anything is missing says NOT COMPUTED with each problem (date, account, amount) instead of a figure. An account of a broker whose export it reads no cash event from (Webull, the generic importer) that moves foreign cash is one of those problems until a `CASHBOOK <book> complete` line says the account's `.tt` cash lines are all its conversions, deposits and withdrawals; the report lists such accounts (NOT READ FROM THE EXPORT; `unread_books` in `--json`). Even a computed v2 figure is labelled "v2 (opt-in, under audit)" and the checklist's fx-cash step stays at attention until you mark that figure reviewed (`tjs checklist --done fx-cash`). `tjs fx-cash --ledger v1|v2` overrides it for one run (`--cash-events` lists what v2 read). The code is `src/taxjson/lib/fx_cash_v2.py` — `build`, `_declarations`; `src/taxjson/lib/cash_events.py` — `collect`, `apply_declarations`, `send_decisions`, `parked_lines`, `refused_lines`.
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
- **Change it when:** you filed earlier years on close timing and must stay consistent; see `tjs option-boundary`.
- **Example:** `option_premium_timing = "grant"`

#### `option_grant_timing_since`
- **Meaning:** contracts written before this year keep close timing — the transition from books filed the old way.
- **Default:** the project's `year` (warned when the books hold a written option; a value later than `year` is warned too).
- **Country:** Canada only.
- **Change it when:** set it once, to the first year you file under grant timing, and keep it unchanged in every later year's project (do not bump it with `year`: `tjs handoff` flags a written option carried across under another timing, taxed twice or in no return).
- **A new year:** `tjs new-year` keeps the previous year's value; when the previous year left it unset (on grant timing), the new year gets `option_grant_timing_since = <the previous year>`, the cutoff that year applied by default, so a premium written then and closed in the new year is not taxed again.
- **A new project:** `tjs init` leaves it commented out (the default, the project's own year), or writes the value another year folder beside it sets; the default is right when your earlier returns netted written options at the close (close timing). If last year's return already reported each premium in the year written (grant timing: a previous taxjson project, or your preparer's), set it to the first year filed that way instead: a contract written last year and bought back or expired this year would otherwise be taxed again at the close. `tjs run` asks when such a contract exists (the premium at stake and the question); the setting, or `tjs checklist --done option-boundary` when last year did not report the premiums, answers it — for the contracts asked then (a contract written before the setting and closed later is asked again).
- **Example:** `option_grant_timing_since = 2025`

#### `option_buyback_loss_superficial`
- **Meaning:** `true` applies the strict s.54 reading: a loss on buying back a written option is superficial when identical options are bought within 30 days and still held.
- **Default:** `false` (a buy-back loss is exempt under either timing).
- **Country:** Canada only.
- **Change it when:** your adviser takes the strict reading.
- **Example:** `option_buyback_loss_superficial = false`

#### `leaps_months`
- **Meaning:** options bought more than this many months before expiry count as LEAPS in `tjs leaps` and `tjs leaps-sum` (views only, no tax figure).
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
- **Meaning:** what a spin-off or merger in a sheltered account (Canada: RRSP, LIRA, TFSA, RESP, RRIF ...; US: IRA, Roth, 401(k), HSA, 529 ...) does when it has no saved election. `"zero"`: it is booked without asking — a spin-off's new shares at $0 cost with the parent keeping its whole cost, a merger's new shares taking the old shares' cost — with one `Info:` line per run; it is never pending, so `run --strict`, `elect --pending` and the checklist do not wait for it. `"ask"`: it is asked like a taxable account's event (the prompt, `elect --pending`, exit 3 without a terminal). Either way an election saved with `tjs elect` wins, and a taxable account is always asked. No tax depends on it: nothing is taxed inside the account, an in-kind move in or out is valued at fair market value, and the superficial-loss / wash-sale rule counts the account's units, not their cost (tax-logic CA-CORP-11 / US-CORP-12). The spun-off shares count as acquired for the superficial-loss rule (Canada, as under every spin-off election); in a US project they are not a wash-sale purchase (as under `tax_free_355`) unless you elect `taxable_distribution_301`.
- **Default:** `"zero"`.
- **Country:** both.
- **Change it when:** you want the sheltered holdings view to show a real cost for every spin-off, and to be asked for it.
- **Example:** `sheltered_elections = "ask"`

#### Views

#### `benchmark`
- **Meaning:** the symbol `tjs technicals` measures each holding's beta and alpha against (a year of daily returns), spelled as the books spell a listing (`XYZQ.TO`, `XYZQ.US`; a ticker.map `QUOTE` line maps it to Yahoo's spelling) or as Yahoo Finance spells an index (`^XYZQ`). A bare word is taxjson's spelling of a coin and is refused unless a `QUOTE` line names it. `--benchmark SYMBOL` wins for one run. A view only: no tax figure reads it.
- **Default:** none: beta and alpha are blank, and `tjs technicals` says so in one `Info:` line.
- **Country:** both.
- **Change it when:** you want beta and alpha in `tjs technicals` without passing `--benchmark`.
- **Example:** `benchmark = "XYZQ.TO"`

#### Retired setting

#### `cross_asset`
- **Meaning:** none any more; still recognised only to warn that it is ignored. Delete it.
- **Country:** both.

### `[accounts.NAME]`

One table per folder under `inputs/` (the folder name is the account name). Names use a-z or A-Z letters without accents (`celi`, not `céli`), digits, `_`, `-` and `.`, starting with a letter, digit or `_`; `COMBINED`, `sheltered`, a name ending in a pipeline suffix (`_raw`, `_base`, `_gains`, `_wash`, `_merged`, `_sorted`, `_filled`, `_mapped`, `_report`, `_tt`, `_manifest`, `_sources`) a name inside another account's work files (`margin_ib` beside `margin`) and two names that differ only by letter case (`Margin` and `margin`) are refused.

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
- **Change it when:** a registered account has a neutral name; `tjs tips` uses it (a US dividend payer in a TFSA is flagged).
- **Example:** `plan = "tfsa"`

#### `crypto`
- **Meaning:** `true` for a Coinbase or Kraken account: crypto prices are filled, rows are dated in `local_timezone`, and the account's dispositions are crypto-assets (Canada Schedule 3 line 7 from 2025; US Form 8949 boxes G–L from 2025, no wash-sale rule).
- **Default:** `false`.
- **Country:** both.
- **Change it when:** the folder holds exchange exports. A crypto file in an equity account (or the reverse) is refused.
- **Example:** `crypto = true`

#### `transfers`
- **Meaning:** `true` keeps the broker's TRANSFER rows (contributions, withdrawals) in the books; `false` keeps them aside as custody evidence (`tjs transfers`).
- **Default:** `false`.
- **Country:** both.
- **Change it when:** a registered account's contributions and withdrawals move shares in and out (the usual setting for an RRSP or TFSA). Keep `false` on a taxable account: a taxable book's TRANSFER rows that do not cancel out are refused (replace them with the purchase history). In a US project `false` also pairs moves between your own taxable accounts so the lots travel with their dates.
- **Example:** `transfers = true`

#### `holdings`
- **Meaning:** positions files `tjs sanity` reconciles against with no arguments (IB statements, RBC Holdings Exports, holdings TOMLs); `tjs run` does not check them (its closing list names `tjs sanity` while any account has them). Without it, the account's snapshots in the year's [holdings/](#holdings-the-years-positions-snapshots) folder are found by themselves.
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
- **Meaning:** every row of an account folder is booked to that one taxjson account. When a statement spans several broker accounts (an IB statement or Flex query covering two IB accounts, statements of different IB accounts in one folder, a Questrade or RBC export holding two accounts' rows, Questrade exports of different accounts in one folder), `tjs run` prints an ATTENTION line naming them (ids masked to their first two characters): right only when they are one tax entity — a TFSA exported with a margin account would land in the wrong book. `true` declares that every broker account in this folder's statements is yours and taxable together; the ATTENTION becomes a one-line note with the masked ids.
- **Default:** `false` (an unquoted `true` / `false`).
- **Country:** both.
- **Change it when:** one export covers two of your taxable accounts (a second IB account exported with the main one). On a `sheltered` account it is refused unless the statement itself shows every account is the same registered plan (Questrade's Account Type column); IB and RBC statements do not name the plan, so there the parse stops — export each plan into its own folder. Questrade exports mixing a registered plan and a taxable account, in one file or in several files of the folder, are refused with or without it; a folder whose only Questrade account has an Account Type that contradicts the account's `type` (a LIRA in a taxable folder, a margin account in a sheltered one) gets an ATTENTION line (`src/taxjson/lib/brokerages/base.py` — `combined_accounts_note`, `combined_accounts_refusal`). In a US project a TFSA, RESP, RDSP or FHSA is a taxable account (no treaty deferral, tax-logic `US-BASIS-09`): its Questrade exports may share a taxable folder with the margin account's under `true`, and only an RRSP, RRIF or locked-in plan counts as registered there.
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

#### `custodian_country`
- **Meaning:** the country of the custodian that holds a taxable account's securities, as an ISO 3166 alpha-3 code (`USA`, `GBR`; `CAN`, `CA` or `CANADA` for Canada). With a country outside Canada, `tjs t1135` counts the account's shares of Canadian issuers (a `.TO` / `.V` / `.CN` / `.NE` listing, or a symbol a `T1135 SYMBOL CA` line declares Canadian) as specified foreign property: category 6 (other property outside Canada), under the custodian's country, at that account's share of the ACB pooled over every taxable account (its units times the pool's cost per unit; tax-logic CA-RPT-02, CA-RPT-18). The same shares in an account held in Canada are not foreign property, and the account's foreign securities keep their own country and category. The account's cash, in every currency, is foreign property there too: a row per currency, category 1, at its cost (tax-logic CA-RPT-20). On a `crypto = true` account it says where the platform is: the cash the account holds (its US dollars and USD stablecoins) is counted under that country, CRYPTO while unset, and not counted with `CAN`. Read by `tjs t1135` only (a registered account is never T1135 property); any other value is refused.
- **Default:** none: the custodian is in Canada.
- **Country:** Canada (refused in a US project).
- **Change it when:** a broker outside Canada holds the account (a US brokerage account, a custodian abroad), not a Canadian dealer that buys foreign shares for you; or a crypto account's platform, once you have checked where it holds your cash.
- **Example:** `custodian_country = "USA"`

#### `brokerage`
- **Meaning:** the `tjs fetch` source of this account (a fetcher plugin's name; `tjs fetch --list` names the installed ones). Accepted whether or not a plugin is installed.
- **Default:** none.
- **Country:** both.
- **Change it when:** you download this account with a fetcher plugin.
- **Example:** `brokerage = "SOURCE"`

#### `account`
- **Meaning:** the broker account id `tjs fetch` downloads, if its source needs one.
- **Default:** none.
- **Country:** both.
- **Example:** `account = "ACCOUNT_ID"` (keep the real id out of anything you share).

#### `query_id`
- **Meaning:** the report (query) id `tjs fetch` runs, if its source needs one.
- **Default:** none.
- **Country:** both.
- **Example:** `query_id = "QUERY_ID"`

### `[estimate]`

Inputs `tjs estimate` (and, in Canada, the instalments current-year basis) uses when the command-line flags are not given; a flag wins.

#### `other_income`
- **Meaning:** income besides these books (employment, interest ...).
- **Default:** `0`.
- **Country:** both.
- **Example:** `other_income = 900`

#### `other_losses`
- **Meaning:** Canada: net capital losses of earlier years applied, in full dollars (netted against the year's gains before the inclusion, up to them). US: the short-term capital loss carryover.
- **Default:** the net capital loss (US: the short-term carryover) the latest close-year lock before the project year carried out (`filed/<year>.json` or `prior_year_record`); with no lock, `0`. `tjs estimate` prints where a carryover it uses came from; with no lock and nothing entered it says its carryovers in are 0 for that reason (`tjs carryover` shows what the books carry in).
- **Country:** both (meaning differs).
- **Example:** `other_losses = 500`

#### `deductions`
- **Meaning:** lines 20700–23500 the AMT allows in full (RRSP, FHSA, RPP ...), with a deduction of foreign tax you claim on line 23200 (ITA s.20(11), s.20(12)): the estimate names the foreign tax no credit takes but does not deduct it.
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
- **Meaning:** net capital losses actually applied on each filed return, by year, `{ YEAR = AMOUNT }` (`tjs carryover`; the US ledger otherwise assumes the $3,000 offset was used). One total per return: losses of two later years carried back to the same year add up in its entry; a closed year takes the part its lock recorded as carried back and a later loss the rest. US: the Schedule D line 21 deduction the return took; the project year's own entry is also what its estimate and `tjs close-year` count as deducted when they carry the loss forward (without it, as far as the estimate's taxable income absorbs it), and the next year's `tjs handoff` flags an entry that differs from what the lock counted.
- **Default:** none.
- **Country:** both.
- **Example:** `claimed = { 2024 = 400.00 }`

#### `carried_back`
- **Meaning:** an answer, not a setting: the net capital loss a year's return carried back on its form T1A, by year, `{ YEAR = AMOUNT }` (`0` for none) or, by the year it went to, `{ YEAR = { TARGET = AMOUNT } }`, for a year `tjs carryover` says it can't tell. A year before the project year with no close-year lock is rebuilt from the books, and its row meets a `claimed` amount from its loss; the return may have carried back nothing (the claim was a later year's T1A), or the same from a loss the books no longer hold. A balance you typed (`[estimate] other_losses`, the balance carried in) does not answer it for you. Set against the books' nets it would say what those years carried back (60 carried in after a loss of 100: 40; less when a gain year lies between, since what it applied is your choice unless `claimed` has its entry; at least that much when no close-year lock comes before, since losses from before your files may be in the balance), and `tjs carryover` prints that figure beside the question. But the nets leave out capital gains and losses those returns can hold (T3 box 21, T5 box 18 not named in `[[capital_gains_dividends]]`, the FX gain or loss on foreign cash of line 15300), and one of them changes the figure, so you are asked and you answer from that year's T1A. An entry and a typed balance that don't add up on the books' nets are both taken as you gave them, and `tjs carryover`, the estimate and `tjs close-year` say what must then be true: part of the balance was carried in from before these books (the balance is above what the entries leave, with no lock before), or those returns had a capital gain (the balance is below it) or a capital loss (above it, after a lock) that the nets don't hold; check both figures against the returns when you see it. Where the project year has a loss left to split and its carry-back still depends on the answer, `tjs carryover` prints the carryforward as "can't tell", the estimate's carry-forward is unknown and `tjs close-year` stops, each naming the year and the exact line for each reading. Every year that could have carried the loss back is asked about, a year with no loss of its own included (a balance it took in can carry back). The year's row then carries back the amount you give (from a loss not in the books too, as a lock's carry-back is), `tjs carryover` shows it as your figure (`carried_back_stated` in `--json`), and the lock records it (`carry_back_stated`). It never replaces a figure a lock records or the books show: an entry that contradicts one is refused, naming both figures, as is an amount above the claims that year could meet (those under the 3 years before it, not yet met), a plain amount for the project year and any entry for a later one. An entry the ledger no longer needs (that year's lock, or a later one, now gives the same answer, or nothing of the project year rests on it any more, as in a taxjson.toml carried into the next year) is an `Info:` note saying it can be removed, and changes nothing. That year's close-year lock in `filed/` answers the same question, and an answer a close recorded (`carry_back_stated` in its lock) is read by the next years' projects, so a taxjson.toml that no longer has the line is not asked again; an entry that differs from the recorded answer is refused, naming both. **By target year.** A T1A names the years a loss is carried back to, and `claimed` holds one total per year a loss was applied to, so with `claimed` amounts under more than one of the 3 years before a loss year, `{ YEAR = AMOUNT }` does not say which was met: `tjs carryover` meets the oldest first. Say the years with `{ YEAR = { TARGET = AMOUNT } }`: `carried_back = { 2022 = { 2021 = 40.00 } }` is the 2022 return carrying 40.00 back to 2021, and `{ 2022 = { 2020 = 10.00, 2021 = 30.00 } }` 10.00 to 2020 and 30.00 to 2021; the year's total is their sum. Both forms can stand in one table for different years. Each target is one of the 3 years before the loss year and has a `claimed` amount of at least what is carried back to it; an amount is a number of 0 or more; anything else is an `Error:` naming the entry, as is an amount above what the carry-backs before it left of that year's claim. Where the project year's carry-back, the loss it carries forward or the years its own carry-back goes to change with the years an earlier carry-back went to, and no entry and no lock says them, `tjs carryover` can't tell (`carry_back_split_cant_tell`), the estimate's carry-forward is unknown and `tjs close-year` stops, each giving a line for every set of figures this year can have, with those figures; a plain total for such a year is taken as the amount and the question about its years stays. Every reading is tried: an amount that is known along each order of the claimed years in reach; for a year with no lock, each of those cut after any year, down to nothing; and every combination across the years (up to 64; past that you are asked). A reading that would leave a later lock short of the carry-back it records is left out, so each line offered is one `tjs carryover` takes. Where the readings agree, nothing is asked. An entry for a year you were not asked about is folded before it is called not needed: if it changes this year's figures it is taken. A year with a close-year lock takes a by-target entry too, when its lock was written before locks recorded the years: its amounts add up to the lock's `carried_back`. For the project year itself, `{ YEAR = { TARGET = AMOUNT } }` says which years its own carry-back goes to (what `tjs carryover` computes is the total; the amounts add up to it, checked once the earlier years are settled); without it the oldest year first is the proposal `tjs carryover`, the estimate and `tjs close-year` print. The lock records the years (`carried_back_to` for its own carry-back, `carry_back_stated_to` for your answers about earlier years), a later year's project reads them there, so the question is asked once, and an entry that differs from a recorded answer is refused, naming both, whether or not this year still rests on it. `tjs init` does not write it; `tjs format` keeps it.
- **Default:** none.
- **Country:** Canada only.
- **Example:** `carried_back = { 2022 = 0 }`; by target year, `carried_back = { 2022 = { 2021 = 40.00 } }`

### `[[distributions]]`

One table per non-cash fund distribution (a reinvested capital-gains distribution, usually each December, that no broker export shows; a return-of-capital factor a fund publishes after the year end). `tjs run` books each as a cost adjustment on the shares every taxable account holds on the record date (units an `OPENING ... cost=unknown` line opens count as held), on the ticker that held them that day (renames and ticker changes followed). Only the cost side is booked: the distribution's income is on the T3/T5 slip, and taxjson adds it neither to the estimate nor to `divs-sum` (the run's note says so). A symbol and date entered in two tables are both applied (they add), with a warning; a return-of-capital entry dated like a return-of-capital ADJUST the books already carry is warned about (the cost would be cut twice: keep one). A malformed entry stops every command. Both countries.

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

T5 box 18 capital-gains dividends the books carry as dividends (no export labels them: IB prints "(Ordinary Dividend)", RBC and Questrade a plain dividend; copy them from the slip): `tjs divs-sum` lists them under CAPITAL-GAINS DIVIDENDS, apart from the dividend totals, and the estimate (`tjs estimate`, `tjs sum --other-income`) moves them from the grossed-up eligible dividends into capital gains. The ACB does not change. One table per payment or per year. An entry that matches no dividend, an amount above the matching dividends or matches in two currencies stops the view naming the entry (`[[capital_gains_dividends]] #2`); a malformed or repeated entry stops every command. Canada only (a US project refuses the table).

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

CRA instalments (`tjs instalments`, and a summary in `tjs estimate`). Canada only; US estimated tax is not modelled.

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

The project's one mapping file, at the project root; one rule per line, symbols case-insensitive (but a `GLOBAL` or `DISTINCT` line whose two symbols differ only in letter case: below), notes after `#`. A symbol is read as the books spell it: upper case, a listing on a Canadian venue `ROOT.TO` (`ZYAB.NE`, `.CN` or `.VN` is `ZYAB.TO`, `FTN.PRA.TO` is `FTN.PR.A.TO`; an `EXTRACT` line's CAD `.V` too), a future `F:` (`/ZZQZ6.US` and `\ZZQZ6.US` are `F:ZZQZ6.US`, an option on one included; every keyword, `MULT` too: two lines naming one future in two spellings are one symbol, so two values for it are refused; a line whose future names no contract once read so, `//ZZQZ6.US` or `/.US`, is a malformed line). A `.V` elsewhere is read as written (it may be a class letter): when the books hold `ROOT.TO` and not `ROOT.V`, `tjs run` warns that the line does nothing, naming the line to write. `tjs run` refuses a map with a malformed line, a rename cycle, one symbol renamed to two targets, a `DISTINCT` pair the renames pool together, or a rename-type line (`GLOBAL`, `TOBASE`, `JOURNAL`, `RENAME`) that joins an option contract or a future with a share listing, an option with a future, or two different option contracts (another expiry, right, strike or market); a respelling of one contract (the same expiry, right, strike and market, the root spelled otherwise: `GLOBAL ZZB.B250620C00010000.TO ZZB250620C00010000.TO`) is allowed. Symbols are matched exactly, suffix included: `GLOBAL ZZOL ZZNW` does not touch `ZZOL.US`. Rules chain (`GLOBAL ZZOLD.US ZZNEW.US` plus `TOBASE ZZNEW.US ZZNEW.TO` sends ZZOLD.US to ZZNEW.TO). An option follows its underlying's line through its root, except where that would land on a contract code the books already trade on the other listing (a USD-strike US option and a CAD-strike Montreal option are different property): the US contract keeps its symbol, with a warning, unless a rule names the option itself (`src/taxjson/bin/taxjson_ticker_map.py` — `guard_option_listing_collisions`). `tjs ticker-map --suggest --write` appends suggested lines. ticker.map holds standing truths only: a ticker change or a journal between two listings happens on a date and is a `.tt` line of an account, date first (`RENAME <date> OLD NEW`, `JOURNAL <date> FROM TO <qty>`; see [.tt files](#tt-files)). A map that still has the legacy `JOURNAL` or dated `RENAME` lines works (`JOURNAL` is read as `TOBASE`), with one Warning per run. `tjs format-map` lays the file out the way `tjs init` writes it: a short header, then the rules in groups, each under a `## --- <Group> ---` heading — Spellings (`GLOBAL`, an undated `RENAME`), Listings of one security (`TOBASE`, `DISTINCT`), Clean-up (`DELETE`), Lookups (`QUOTE`, `EXTRACT`, `CRYPTO`, `T1135` and the market lists), then Retired (`TRADINGVIEW`) and Unrecognized (lines `tjs run` cannot use, kept as written) when there are any. It also migrates the dated events: each `JOURNAL A B` becomes `TOBASE A B`, and each dated `RENAME OLD NEW YYYY-MM-DD [late=…]` moves, with its comments, to `inputs/<account>/renames.tt` as `RENAME YYYY-MM-DD OLD NEW [late=…]` — in the first account whose books carry the change (else whose books hold the symbols, else the only account, else the first taxable one), among the accounts whose own `.tt` lines of the change agree with its `late=`, one per account kind whose books hold the symbols: a `.tt` RENAME applies to every account of its kind. When that home would change some account's `late=` (an account with no line of its own follows the map line's), the line goes to the first account whose copy keeps every account's choice; when none does, nothing is written and the refusal names each such account with the line to add to its own `.tt` file. An account that did not hold OLD before the date never follows the event's `late=` and is not compared on it. Before writing, it simulates the run: the rename events booked after the move (the project's `.tt` lines plus the moved ones) must be the ones booked now in every account (an account without books yet: on every change that still reaches its kind), or nothing is written (exit 2, naming the lines that disagree); a moved line repeating a `.tt` line written differently is reported. Every target is checked first (a link to outside the project — the file or its `inputs/<account>` folder — is refused), the `.tt` files are written before the map, and a block a file already holds is not added again. The dry run shows the map diff and the `.tt` additions, and a `.tt` `JOURNAL` line for each migrated `JOURNAL` whose listings the holdings show long and short; `--check` fails while the map is not formatted, a migration is pending or `tjs run` refuses the map. A header an earlier taxjson version wrote (any `tjs init` template, the earliest projects' header, `tjs migrate`'s line; re-wrapped, re-cased or with your notes directly below it) is replaced by the current one; a copy you edited is kept, with an Info line, and so is a comment of yours that still describes `JOURNAL` or a dated `RENAME` as a map rule. Your order is kept within a group, a comment directly above a line moves with it, and the parsed map (plus the moved lines) must mean the same (`src/taxjson/lib/ticker_map_format.py` — `format_map`, `GROUPS`, `Moved`; `src/taxjson/lib/dated_events.py` — `home_accounts`; `src/taxjson/lib/rename_migration.py` — `plan_migration`). The parsers are `src/taxjson/lib/ticker_map_rules.py` — `merge_renames`; `src/taxjson/lib/map_file.py` — `_parse_map_file`; `src/taxjson/lib/ticker_map.py` — `parse_side_line`, `RENAME_KEYWORDS`, `SIDE_KEYWORDS`, `MARKET_KEYWORDS`.

### Rename rules

#### `GLOBAL`
- **Form:** `GLOBAL FROM TO`
- **Meaning:** a plain rename in every stage (a security's true ticker). Between two bare crypto codes it is applied by the Coinbase and Kraken parsers before a row is read: it folds a staked or wrapped code into its coin (taxjson ships no such fold), or adds a Kraken legacy code.
- **Country:** both.
- **When:** a broker's odd spelling, a Questrade internal code the run could not resolve, a coin alias.
- **Questrade internal codes:** Questrade's website export writes the rows of shares transferred in from another broker under a code (one letter and digits, `X000123`) instead of the ticker. `tjs run` resolves a code to a ticker from the account's own trades described like it, else from the transfer it arrived by (another broker's transfer out of the same quantity, from 10 days before the arrival to 3 after, under an agreeing name), else from exactly one listing elsewhere in your books whose name equals it; the share designators (warrant, right, preferred, the class letter) must agree over all of the code's descriptions. Every row of the code is then booked under that ticker, said in one note per account; `tjs transfers` lists the codes. A code nothing identifies keeps an ATTENTION line, with a near miss and its `GLOBAL` line when there is one: add the line. Any ticker.map rule naming the code wins over the inference (`src/taxjson/lib/symbol_codes.py` — `resolve`, `PAIR_DAYS_BEFORE`, `PAIR_DAYS_AFTER`; tax-logic CA-ACB-CODES / US-BASIS-CODES).
- **Example:** `GLOBAL X000123.US ZZQ.US` · `GLOBAL ETH2 ETH`
- **Two spellings, one security:** `GLOBAL ZYXa.US ZYXA.US`, two symbols that differ only in letter case, is the one line read in its case: the exports' spelling `ZYXa.US` is the security `ZYXA.US`. taxjson reads an export's symbol in any case as one security (upper case), but stops (`tjs run: error: the exports spell ZYXA.US 2 ways, differing only in letter case`) when the exports spell one symbol two such ways, until this line or the `DISTINCT` one says which they are (`src/taxjson/lib/symbol_case.py` — `collisions`, `collision_message`).

#### `TOBASE`
- **Form:** `TOBASE FROM TO`
- **Meaning:** two listings of one security (a US line and its TSX line) are one ACB pool / one security; applied when converting to the base currency. The raw holdings view keeps them apart except where the broker's transfer rows prove a move.
- **Country:** both.
- **When:** an interlisted share you trade on both lines that the run did not join itself (`tjs ticker-map --suggest` proposes it; `tjs journals` lists each journal between the two listings, the line that pools them, or why it is not joined).
- **Example:** `TOBASE ZZQ.US ZZQ.TO`

#### `JOURNAL` (legacy)
- **Form:** `JOURNAL FROM TO`
- **Meaning:** read as `TOBASE FROM TO` (one security), with one Warning per run; `tjs format-map --write` rewrites it. It no longer nets the two listings in the holdings view: a journal's own transfer legs move the units — the broker's (a Questrade BRW pair, RBC's TFR legs, IB's InterDepot) or, when the export lacks them, a `.tt` line `JOURNAL <date> FROM TO <qty>`. The missing-history checks read a journal the books show (a join of the run, a Questrade pair, RBC's J~ reference on the two transfer legs, a `.tt` JOURNAL line) with that day's buys first. `tjs journals` lists every broker journal between two listings with the line that pools it (yours, `ticker.map:N`, or the run's own join).
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
- **Meaning:** two listings are separate securities; changes no symbol, undoes an automatic cross-listing join and records the decision. When you need it: when taxjson would otherwise join or ask about the pair — a journal it pairs, a look-alike rename, the cross-listing loss warning, or a MAP-GAP pair of `tjs ticker-map --suggest` — and they are two securities. It silences any of these for the pair. Not needed when the exports already show the two apart: `tjs ticker-map --suggest` asks nothing about a Canadian line named as a depositary receipt (a CDR) or on a receipt venue, or a pair whose names are two companies'; nor, in Canada, about a CDR and its US share the interlisted master knows (tobase.map writes no `DISTINCT` line for it since v0.27.1: a journal between the two is never joined, and neither the loss warning nor a MAP-GAP pairs them). `tjs journals` lists a journal between the two as refused, naming this line. A symbol written bare (no market suffix) beside a share listing also names its US listing, as the books spell a broker's bare US ticker: `DISTINCT ZZR ZZR.TO` keeps `ZZR.US` and `ZZR.TO` apart too (the pair as written stays: a bare symbol may be a coin or a broker's code), with an Info line when the books hold `ZZR.US` and not `ZZR`. A `GLOBAL`, `TOBASE`, `JOURNAL` or undated `RENAME` line written that way is not re-read (the line would move pools): when the books hold the US listing and not the bare symbol, `tjs run` warns that it joins nothing, naming the line to write (`src/taxjson/lib/ticker_map_rules.py` — `distinct_spellings`, `listing_spelling_notes`).
- **Country:** both.
- **Example:** `DISTINCT ZZR.TO ZZR.US`
- **Two spellings, two securities:** `DISTINCT ZYXa.US ZYXA.US`, two symbols that differ only in letter case: the exports' spelling `ZYXa.US` is a security of its own, booked with its lower-case letters as a class, `ZYX.A.US` (a broker's class letter written in lower case); its trades and corporate actions alike. Only a spelling whose lower-case letters end its root after an upper-case start can be kept apart (`src/taxjson/lib/symbol_case.py` — `class_spelling`).

#### `RENAME`
- **Form:** `RENAME OLD NEW` (undated); legacy: `RENAME OLD NEW YYYY-MM-DD [late=fold|late=separate]`
- **Meaning:** without a date, `GLOBAL OLD NEW`. The dated form is legacy: a ticker change on a date is a `.tt` line `RENAME YYYY-MM-DD OLD NEW [late=…]` (the `.tt` RENAME action below). A dated line here still works the same way, with one Warning per run; `tjs format-map --write` moves it to a `.tt` file. `tjs renames` names each dated rename's source (a broker row, an IB contract id, a `.tt` line, a legacy ticker.map line) and lists the look-alike renames the exports show as suggested.
- **Country:** both.
- **Example:** `RENAME ZZOLD.US ZZNEW.US` · legacy `RENAME ZZOLD.US ZZNEW.US 2025-04-01 late=fold`

### Lookups (change no symbol in the books)

#### `SIMILAR`
- **Form:** `SIMILAR A B [C ...]`
- **Meaning:** look-alike funds you want watched together: two or more share listings, each with its suffix (never an option, a future or a bare ticker), such as two issuers' funds on one index or a fund and its Canadian wrapper. They stay separate securities: their own ACB pools (US: their own lots), nothing denied, because the superficial-loss rule reaches identical property only (IT-387R2) and §1091 substantially identical securities, which the IRS has not defined for funds of different issuers; whether such a pair is one is your call. With the line, a loss on one in a taxable account with another bought within 30 days before or after it in any of your accounts (in Canada still held at day 30) is a Warning of every `tjs run` naming the pair, the dates and the line (`--strict` never stops for it), the checklist's wash-reviewed step lists it as a manual decision, `tjs buy-check` and `sell-check` add a caveat line (SAFE\*, never UNSAFE), `tjs rebuy --details` names the similar listings a buy of which is flagged, and `tjs wash-radar` lists each group's statuses. Lines sharing a symbol are one group; a member a `GLOBAL` or `TOBASE` line moves is read under its booked symbol (tax-logic CA-SL-20 / US-WASH-26).
- **Country:** both.
- **When:** before a tax-loss swap between two funds you consider alike, so the planning tools and the run say when the pair is inside a window. Delete the line to stop the warnings; nothing in the figures changes either way.
- **Example:** `SIMILAR ZZSP.US ZZVO.US ZZVF.TO  # S&P 500`

#### `QUOTE`
- **Form:** `QUOTE SYMBOL YAHOO_SYMBOL [QTY_RATIO]`
- **Meaning:** the Yahoo Finance spelling `tjs harvest` and the price chain quote; QTY_RATIO (default 1) converts the position's quantity into the quoted ticker's units.
- **Country:** both.
- **Example:** `QUOTE ZZOLD.TO ZZNEW 0.25`

#### `CRYPTO`
- **Form:** `CRYPTO SYMBOL YAHOO_ID`
- **Meaning:** the Yahoo id of a coin whose ticker collides with another asset; fill-crypto, `tjs crypto-sends` and `tjs harvest` quote `YAHOO_ID-USD`. These lines are the only coin ids (taxjson carries no built-in table): without one a coin is quoted as `SYMBOL-USD`. Yahoo gives a ticker two assets share a number (`SYMBOL<number>-USD`): find your coin's id on finance.yahoo.com and write it here. A trailing `-USD` is accepted. Editing a line re-prices at the next `tjs run`.
- **Country:** both.
- **When:** the run names a coin whose lookup failed (with this line to add), whose Yahoo close is more than 5 times off its own trade prices within 7 days, or that is priced as `SYMBOL-USD` while the price cache holds a numbered id of the same ticker from earlier runs (ATTENTION lines on the console and in the account's `.sum`).
- **Example:** `CRYPTO ZZC ZZC12345`

#### `EXTRACT`
- **Form:** `EXTRACT DESCRIPTION WORDS | CURRENCY | SYMBOL`
- **Meaning:** a parser symbol override: a broker row whose description contains the words (whole words, any case) and whose currency is CURRENCY (`*` = any) gets SYMBOL. First matching line wins; options and futures are never rewritten.
- **Country:** both.
- **When:** the currency-to-suffix rule mislabels a security (a TSX-only USD unit). `tjs run` warns when a `.US` symbol carries such a unit beside another company ("names two securities") and `tjs ticker-map --suggest` offers the line, its words the shortest run common to every description of the fund in the project and in no other row's (an RBC export's own warning for a US-dollar unit: the longest start of the fund's name that every US-dollar row of it in the account carries and no other symbol's does; the words must match the broker's description as written: `US DLR` and `U S DLR` are different words).
- **Example:** `EXTRACT Sample US Dollar Fund | USD | ZZD.U.TO`

#### `T1135`
- **Form:** `T1135 SYMBOL COUNTRY [CATEGORY]`
- **Meaning:** the T1135 country of SYMBOL: an ISO 3166 alpha-3 code, or `CA` / `CAN` / `CANADA` / `EXCLUDE` for not specified foreign property; any other value stops the report. It is the first of the steps that decide a security's country, ahead of the ISIN an export gives, tobase.map's issuer country, the same security's ISIN in another account, and the listing suffix (`tjs t1135` shows which step decided each row: [commands.md](commands.md#tjs-t1135); tax-logic CA-RPT-02). Write it where the report's country is not the one the form asks for (the issuer's country of residence, where its ISIN names only where it is registered), for a security the report names for review, and for one whose country is `assumed` wrongly. A line on any symbol of a security decides it before and after a ticker change, when the change is a rename of one security (the same ISIN on both symbols where the exports carry one, else a change into a symbol with no position): a symbol's own line first, then the nearest earlier symbol's, then the nearest later one's. It does not reach a merger partner's shares, or an old ticker bought again after the change: write those their own line (CA-RPT-21). A listed option keeps its own listing's country unless a line names the contract (CA-RPT-22). CATEGORY, optional, is the form's category of the property, 1 to 6, with a foreign country: taxjson takes 6 for an option, a futures contract or crypto and 2 for any other listed security, and leaves units or a fund the interlisted master knows for you to set (tax-logic CA-RPT-18). Write the country the report shows to set only the category (`T1135 ZZU.US USA 4`). An option on a listing a `TOBASE` line pools under a Canadian listing is booked under that listing's code and read as Canadian (taxjson's filing position, tax-logic CA-RPT-17); a `T1135` line on the contract's booked symbol sets another country.
- **Country:** Canada (read by `tjs t1135`).
- **Example:** `T1135 ZZQ.US CA`, `T1135 ZZT.US USA 4`

### Market lists (extend or override the shipped data)

The lists taxjson cannot read from an export ship in `src/taxjson/data/markets.toml` (read only by `src/taxjson/lib/markets_data.py` — `data`; `src/taxjson/lib/markets.py` — `overrides` adds a project's ticker.map lines to them). Whenever a built-in list decides an outcome the run prints one note per symbol naming the line that would change it. The file also states a listing convention: `[usd_unit_class]` makes a Canadian-listed fund's US-dollar units `ROOT.U.TO` (suggested for a symbol collision, and used for a Questrade currency journal's USD leg when the account's own rows show no USD listing of the security; an `EXTRACT` or `GLOBAL` line overrides it for one security; `src/taxjson/lib/markets.py` — `usd_unit_listing`). And it says which ISIN countries have a home market (`[isin_country_suffix]`: `CA`, `US`, `GB`, `AU`): an Interactive Brokers income row of any other ISIN country (`[isin_domicile_suffix]` gives `IE` the suffix looked for first, `.L`; the rest `.US`) is booked only on a listing the account holds, the statement's instrument list gives the ISIN, or a ticker.map line names: a line naming that symbol (`QUOTE ZZQ.L ZZQ.L`, a `GLOBAL ZZQ.L ...` line that renames it), not a line naming another listing of the ticker; with none the parse stops, naming the line (docs/brokers.md, "An ISIN that names no market"). These ticker.map lines change one entry each:

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

#### `SECFUT`
- **Form:** `SECFUT ROOT` or `SECFUT ROOT NO` (ROOT: the futures root, without the month code: `F:ZZQZ6.US` is `ZZQ`)
- **Meaning:** a security future (a single-stock or narrow-based index future): not a §1256 contract for a holder who is not a dealer (IRC §1256(b)(2)(A)), so its gain or loss is a capital gain or loss of its own term on Form 8949, not Form 6781's 60/40, and a loss on it can be a wash sale. Without the line every future is taken as a §1256 contract.
- **Country:** US.
- **Example:** `SECFUT ZZQ`

#### `EVENING`
- **Form:** `EVENING ROOT` or `EVENING ROOT NO`
- **Meaning:** an option root with a Cboe evening session: a fill from 20:15 ET is dated the next trading day.
- **Country:** both.
- **Example:** `EVENING ZZX`

#### `MULT`
- **Form:** `MULT SYMBOL N`
- **Meaning:** an option's contract size where the export does not state it (SYMBOL is the option or its root); used for assignments, as the replacement size in the superficial-loss / wash-sale rule, and by the total check of a `.tt` option line (qty × price × N, before the line's own `xSIZE`; an adjusted series' total at × 100 passes too, a mini's 10 does not). In the generic importer it sizes a row's cash only for a mini's 10, or when the row's amount is at N (and not at × 100, within a commission of up to 5%): an adjusted series' premium is still × 100.
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

- **Where:** in a multi-year project, one file beside the year folders that every year reads ([`tobase_map`](#tobase_map) `= "../tobase.map"`); in a single-folder project, beside ticker.map. `tjs init --country canada` writes it (at the top in the year layout, setting the year's `tobase_map`), `tjs new-year` keeps the setting (no per-year copy), `tjs update-tobase-map` keeps it in step with the installed master (dry run by default, `--write` applies it, the previous file kept as `tobase.map.bak`; in a year folder that reads the shared file, or in the folder holding the years, it updates that one file and says it applies to every year). A US project gets none (and does not read one: tax-logic US-XLIST-05).
- **Per-year copies (before v0.27.1):** `tjs migrate` in the folder holding the year folders (or in any year folder) makes them one: identical copies become one `tobase.map` there at once, each year's `taxjson.toml` gains `tobase_map = "../tobase.map"` and each copy is removed, kept as `tobase.map.bak`. Copies that differ are listed — the lines of yours (your own lines, an edited marked line) that only another copy has, and how many master lines of another master version — and nothing is written without `--write`, which keeps the newest year's file (the lines listed are not carried over: add the ones you still need). `--dry-run` shows the plan. Until then the years keep a copy each (`tjs years` says so); `new-year` from such a year copies it on, with a note naming `migrate`.
- **Form:** a header (the taxjson version, the master's sources and a `# master-generated: YYYY-MM-DD` line; for a shared file, that it applies to every year), then `TOBASE US CA` lines in sections — `## --- Exchange-listed pairs (one security, one ACB pool) ---`, then `## --- OTC listings of the same shares ---`. No `DISTINCT` line: look-alike listings are never joined, and a depositary receipt the master knows stays apart from its US share without one (the `DISTINCT` lines earlier versions wrote are retracted by `update-tobase-map`: "not needed: look-alike listings are never joined"; one you edited is kept and flagged ATTENTION). Each generated line ends `# master:<share-class FIGI>`; an ended interlisting adds `until=YYYY-MM-DD` (or `until=unknown`), and `ended=SYMBOL` when only that listing ended. Example: `TOBASE QZAB.US QZA.TO  # master:BBG000000Q01`. A pair is booked under the issuer's home listing: the Canadian line, or for a company the master knows is domiciled outside Canada (its `domicile`, the country of its share class's ISINs) the US exchange line (`TOBASE QZC.TO QZCU.US`), so its dividends and its T1135 status stay foreign. An issuer whose domicile the master does not know is booked under the Canadian line: a `T1135` line in ticker.map corrects its T1135 country.
- **Meaning:** read with ticker.map as if its lines were written there (tax-logic CA-XLIST-06): the two listings are one ACB pool and one security for the superficial-loss rule. **ticker.map wins:** a pair is not applied when a ticker.map rule decides one of its listings — a `TOBASE`, `JOURNAL`, `GLOBAL`, `DELETE` or dated `RENAME` of either listing, a `TOBASE` that books another listing under the one the pair would move, or a `DISTINCT` pair (another listing joining the same base is no conflict); when the two disagree the run says so in one Info line (`tobase.map: ticker.map decides N of its pair(s) otherwise`), naming each pair and the line that wins; a pair ticker.map pools the same way is silent. To keep a pair apart, write `DISTINCT US CA` in ticker.map; to pool it otherwise, your own `TOBASE` line. A line that cannot be used (not `TOBASE` / `DISTINCT`, a contradiction) stops the run like a ticker.map problem, named `tobase.map:N`. Without a ticker.map beside it, tobase.map is not read (a Warning; `update-tobase-map --write` creates an empty ticker.map).
- **`.V` and `.TO`:** in a Canadian project a TSX Venture listing and its TSX spelling are one listing for every `TOBASE` and `DISTINCT` line of ticker.map and tobase.map: `TOBASE ZZV.US ZZV.V` also books `ZZV.TO` as `ZZV.V`, `DISTINCT ZZW.US ZZW.V` also keeps `ZZW.TO` apart. The reverse needs nothing: Questrade, RBC, IB, Webull and a `.tt` line book a Venture line in Canadian dollars as `ROOT.TO`, so the books never hold `ROOT.V`. The master writes the `.TO` spelling. `GLOBAL` lines are matched as written.
- **Options:** as with any `TOBASE` line, the options of the listing a pair moves are booked under the base listing's code (`QZAB270115C00010000.US` as `QZA270115C00010000.TO`; the superficial-loss rule then sees a call as the shares' replacement), except where the account trades that code natively; `tjs t1135` then reads them by the base listing's country (a `T1135` line in ticker.map sets another). This is taxjson's T1135 filing position (tax-logic CA-RPT-17): a US-listed option on a Canadian issuer pooled under its Canadian listing is not specified foreign property, since a right to acquire shares of a Canadian-resident corporation (which are not specified foreign property) is not caught by s.233.3(1); CRA's T1135 guidance does not address listed options.
- **Ended interlistings (`until`):** never deleted (the years they traded still pool them). A row in an ended listing dated after its `until` is a Warning naming the line: the ticker may now be another security's (after an acquisition a US ticker is often given to another company). If it is, write `DISTINCT US CA` in ticker.map. `until=unknown` gives no such Warning. When the master knows the ticker names another security today, the line carries `reused_by="<name>"` and **every** row of that listing is a Warning, whatever its date (a reused ticker in the books may be either company). The maintainer's build refuses an ended US ticker that names another security today unless the reuse is recorded.
- **The issuer's country (`country=`):** a pair booked under the US line of an issuer the master knows is domiciled outside Canada carries the issuer's country (ISO alpha-3, from its ISINs): `tjs t1135` reads that symbol as that country — `BEP.US` (the TSX-held `BEP.UN` units booked under it) as Bermuda (`BMU`), not the `.US` suffix's USA. A `T1135 SYMBOL COUNTRY` line in ticker.map overrides it, and a `T1135` line naming a listing a `TOBASE` line books under another symbol applies to that symbol (the "matches no symbol" warning names the symbol it is booked as). Effects of the home listing on the books: a foreign issuer's TSX-held units (BEP.UN, BIP.UN, ...) are foreign property and pay foreign dividends; a Canadian issuer's NYSE- or Nasdaq-bought shares and their options, booked under the TSX line, are not foreign property (they leave the T1135) and pay Canadian dividends.
- **Retractions:** a listing the master shipped in error is retracted (recorded in the master with its reason; never added again). `update-tobase-map` removes its line when you have not edited it, and flags an edited one (ATTENTION, kept).
- **Not checked as unused:** `tjs ticker-map --suggest` never lists a tobase.map line as an unused rule (most pairs are there for a security the books may hold one day), and never reads a tobase.map pair as a sighting of a cross-listing (a TSX Venture junior sharing a US company's letters is no pair to verify). It lists your own ticker.map `TOBASE` / `DISTINCT` lines that tobase.map states identically under "Covered by tobase.map (delete?)" (JSON `covered`): keeping one is harmless — it holds even if the master later retracts the pair; on a terminal `--write` asks keep / delete for each (keep is the default), `--all` never deletes one. `tjs format-map` leaves tobase.map alone (it lays out ticker.map only; `update-tobase-map --write` lays tobase.map out).
- **Marked lines and your edits.** Each master line ends `# master:<FIGI>:<check>`, the check a short hash of its rule as written: a line whose rule no longer matches it (another target, the direction reversed) is your edit — kept as written, listed, and the master's line for the same listing is not added beside it (a marker without the check, from before v0.27.0, is your edit when the master could not have given its rule). `update-tobase-map` adds new interlistings and listings (the OTC ones too), annotates ended ones, removes an unedited marked line the master no longer gives or retracted, adds the new line of a US ticker change beside the old (when both already book under the same listing nothing more is needed; otherwise it names the dated `.tt` `RENAME <date> OLD NEW` event to write), and keeps your own unmarked lines, your comment lines (each with the line below it), your words after a marker, and any line that is not `TOBASE` / `DISTINCT` (listed: the run refuses it — it belongs in ticker.map).
- **Opting out of a master line:** delete it (or comment it out). The next `update-tobase-map --write` records a deleted line as `# removed: master:<FIGI> TOBASE A B` in a section of its own and never adds it back (a line of a listing new since your file's master is added, never taken for a deletion). To have it back, delete the `# removed:` line and copy the master's line back in (or write the pair in ticker.map). Its pairs that your ticker.map decides otherwise are reported, never changed. Without a tobase.map it lists the pairs that would change the books (a pair whose listing the books hold: both listings held — one pool; one — the rows booked under the Canadian listing) and writes nothing without `--write`.
- **Years:** with one shared file, `tjs years` names it and the years reading it, and `tjs years --diff A B` / `tjs align` compare each year's `tobase_map` setting, never the file. With per-year copies (before v0.27.1) `tjs years` counts each year's tobase.map lines that differ from the newest year's, `--diff` lists them, `align` never copies them, and both name `tjs migrate`.
- **Checklist:** the `tobase-map` item reads the file the year reads (a shared one named `../tobase.map, shared by every year`): attention while it is older than the installed master (its `# master-generated` date) or still holds a `DISTINCT` line an earlier version wrote, a step to read when there is none.
- **Country:** Canada (`tjs update-tobase-map` is refused in a US project). The US engine will read the same master as the substantially-identical groups of the wash-sale rule; until then a US project's listings are joined only by its own ticker.map lines (US-XLIST-05).
- **Code:** `src/taxjson/lib/tobase_map.py` — `compute_overlay`, `overlay_for`, `render`, `plan_update`, `until_findings`, `books_changes`, `receipt_pairs`, `plan_shared`, `apply_shared`; `src/taxjson/lib/project_layout.py` — `tobase_map_path`, `tobase_setting`, `tobase_layout`; `src/taxjson/lib/map_file.py` — `_parse_map_file`; `src/taxjson/cli/maps.py` — `cmd_update_tobase_map`, `_tobase_root_year`; `src/taxjson/cli/migrate.py` — `_migrate_shared_tobase`; `src/taxjson/pipeline/stages.py` — `_say_tobase_map`, `_say_tobase_until`; `scripts/build_interlisted.py` — `build`, `merge_previous`.

## `tjs update-tobase-map --json`

A stable schema for programs (new keys may be added; none is renamed or removed while `schema_version` is 1). Code: `src/taxjson/cli/maps.py` — `cmd_update_tobase_map`, `_tobase_map_first_report`, `_tobase_map_update_json`.

| Key | Meaning |
| --- | --- |
| `schema_version`, `exists` | `1`; whether the project has a tobase.map |
| `master_generated` | the installed master's `generated` date |
| `written` | whether this call (`--write`) wrote the file |
| `ticker_map_decides` | `[{"line", "why"}]`: the master's pairs ticker.map decides otherwise (reported only) |

Without a tobase.map: `lines` (how many pair lines one would hold) and `changes_books` (`[{"line", "how"}]`: the pairs that change these books). With one: `file_master_generated` (its `# master-generated` date, or `null`), `added` (`[{"line", "figi", "section"}]`), `added_in_books` (the added lines naming a symbol of the books), `ended` (`[{"line", "until"}]`), `retracted` (`[{"line", "why"}]`: removed), `retracted_but_edited` (`[{"line", "why"}]`: kept, check them), `edited` (lines), `edited_replaces` (`[{"master", "yours"}]`), `unknown_figi`, `opted_out`, `removed_by_you` (lines), `not_tobase_lines` (kept as written), `made_ticker_map`, `ticker_changed` (`[{"old", "new", "until", "pooled"}]`; `pooled`: both already book as one symbol, nothing to write).

## .tt files

Hand-entered rows, any `*.tt` file in `inputs/<account>/` (TaxTrak format); `tjs run` merges, sorts and de-duplicates them into the account. One space-separated line per row; `#` starts a comment; numbers take a decimal point (a thousands comma is fine, a decimal comma is refused); symbols are upper-cased and carry their listing suffix (`.TO`, `.US`; options in OCC form, `ZZQ271217C00036000.TO`), and a future's `/` or `\` prefix is read `F:`, the brokers' spelling (`/ZZQZ6.US` is `F:ZZQZ6.US`, one pool and, for the same content, one row id with the export's rows; a line that differs from another only in the prefix is an identical line; one that names no contract once read so, `//ZZQZ6.US`, `/F:ZZQZ6.US` or `/.US`, is refused, naming the line) — write them as the account's exports spell them: a suffix that is not a market (`ZZQ.TSX`, `ZZQ.CA`) is a warning naming the line, since it would be an ACB pool of its own. The rows that take a time column need it (`09:30:00` is fine): the field positions depend on it. A line has one date, used as both trade and settle date: write the date that matches `tax_date` (the settlement date under `settle`). Any row line with a time column may end with `settled=YYYY-MM-DD`, the row's settlement date when it is not the line's date (a dividend paid on 2025-12-31 that settled on 2026-01-02: `DIVIDEND 2025-12-31 … settled=2026-01-02`; a trade written with its trade date): the line's date is then the trade or pay date, and the FX-on-cash ledgers walk the row's cash on the settlement date. It is on or after the line's date and at most 366 days after it (an earlier one, a later one — read as a typo'd year — or one that is no date is refused, naming the line) and is part of the row's id, as a book row's settlement date is. The other way round, a line written with its settlement date may end with `traded=YYYY-MM-DD`, the row's trade date (`BUYSELL 2025-12-31 … traded=2025-12-30`): on or before the line's date and at most 366 days before it (a later one, an earlier one or one that is no date is refused), part of the row's id too; a trade, ASSIGN, TRANSFER, SPLIT, ADJUST or DISALLOW line takes it, never an income, tax, interest or fee line, whose date is the day it was paid (refused: such a line takes `settled=`), and never an OPENING line (one day; `settled=` neither); the tax year still follows `tax_date`, and `tjs t1135`'s cash on trade dates and the cash ledgers' order read it. A line takes one of the two, never both (refused), and neither twice. A line that ends with `not_tt=…` is one `tjs events` printed for a row no `.tt` line can say (`not_tt=in_kind`: a move in kind the run booked; `not_tt=cash_leg`: a reward's or a coin fee's INTEREST, FEE or TAX row; `not_tt=dates`: a row that settles before the day it trades or is paid, or more than 366 days after): it is refused, saying why — delete it. Rows at one date and time are taken in file order, as an export's rows are: write a same-day sale and rebuy in the order they happened. Validate a file with `tjs check-tt` (every account's files; `tjs check-tt inputs/margin/x.tt` one file), which names every malformed line with its file and line; `taxjson-convert-tt --account margin inputs/margin/x.tt` prints the parsed rows and stops on the first. The reverse, `taxjson-convert-tt book.json out.tt`, writes each row's settlement date (`--date-basis trade` for a trade-basis project) with its income facts, contract size, warrant exercise and stock-dividend mark, and keeps both dates of a row whose two dates differ: one whose line shows its trade or pay date (an income, tax, interest or fee row; a trade under `--date-basis trade`) ends with `settled=YYYY-MM-DD`, a trade whose line shows its settlement date (`--date-basis settle`) with `traded=YYYY-MM-DD`, and either file reads back to the same rows. A row whose dates no line says (it settles before the day it trades or is paid, or more than 366 days after) is refused, naming the row, with nothing written. It also writes the cash facts the FX-on-cash ledgers read of the row (`cash_leg=`, `refunds=`: under `BUYSELL`, `ADJUST` and `DIVIDEND` below), so the file moves the same currency on the same days as the book; a row whose line could not say that is refused, naming the row and why, with nothing written: a move in kind `tjs run` booked from the account's transfer rows (a row of type `transfer_book_value` or `in_kind_…`: convert the account's parsed book instead), a reward's or a coin fee's INTEREST, FEE or TAX row (no token on those lines says it); `tjs events PERIOD ACCOUNT` prints the books' rows in this format to copy from, with the same tokens (both writers append them with `tt_tail`). A `key=` token given twice on a line is refused, both values named. The parser is `src/taxjson/lib/tt_format.py` — `parse_tt_line`, `parse_opening_line`, `parse_inkind_line`, `parse_journal_line`, `parse_rename_line`, `expand_acquired`, `_VALID_ACTIONS`, `_SUGAR_ACTIONS`, `_EVENT_ACTIONS`. The dated events (`JOURNAL`, `RENAME`) are written date first with no time column; `tjs run` books them (`src/taxjson/lib/dated_events.py` — `read_declarations`, `settle_journals`, `write_sidecars`) and records each in `work/dated_events.state` with its source (`tt`, `map`, `ib-conid`, `broker`) and place.

#### `BUYSELL`
- **Form:** `BUYSELL DATE TIME SYMBOL QTY CUR PRICE TOTAL [FEE] [xSIZE]`; a stock dividend's new shares end with `type=stock_dividend [ex=DATE] [record=DATE]`; a part of a futures book already on the settlement basis with `type=futures_settlement [settle=LEG,MONEY,PL,CUR[,RATE]]`; a broker's cancellation of a trade with `type=trade_cancel`; the cash facts `cash_leg=none|reward` and `refunds=DATE:AMOUNT[;DATE:AMOUNT…]` (below).
- **Meaning:** a trade. QTY positive = buy, negative = sell; TOTAL is the net cash (your confirmation's net amount): buy = qty × price + fee, sell = qty × price − fee, written positive. A sale whose fee exceeds its gross (a penny option close) has a negative total, read when the line's fee explains it; any other negative sale total is refused, and a `0` there is warned about (it leaves the excess commission out of the loss). A total more than 1% (at least 0.05) off qty × price ± fee is booked as written and warned about on the run's console (file:line, both figures); `run --strict` stops on it and `tjs checklist` lists it. A line with price 0 states its amount by the total alone and is not compared. A buy's total written negative (the cash paid out) is its cost all the same: the magnitude is booked, in the native and the base-currency figures alike (`taxjson-convert-tt` writes the total as the book holds it, so the row's id stays the one its source has). A future (`F:` or `/`) can trade at a negative price, where a buy receives money and a sale pays it: that line carries its size and a total of qty × price × size + fee (a buy), − fee (a sale), with its sign (`BUYSELL 2025-04-20 10:00:00 F:SAMPLK5.US 1 USD -5 -5000 0 x1000`: a 5,000 credit); a total of the other sign (however small) or more than 1% off, or a line with no size, is refused, naming the line. An `ASSIGN` line of a future is refused at any price, naming the line: only BUYSELL fills of a futures contract are booked (on their settled P/L), so the contract an option on a future delivers is written as a `BUYSELL` line. Nothing else trades below 0: a negative price on a share, an option or an option on a future is refused, naming the line. A futures buy with price 0 and a negative total is refused too (a cost in the cash sign or a negative price's credit: write the cost positive, or the price). `xSIZE`, after the fee, is a contract size other than an equity option's 100 (`x1000` for a CL futures option, `x50`, `x0.1`, `x1000000`; an exponent such as `x1e+06`, as v0.37.0 and earlier wrote it, also reads): the check then uses qty × price × size, and a futures line (`F:` or `/`) is checked only when it carries one. `tjs find-missing-history --write-purchases` drafts these lines from a broker's cost into `inputs/<account>/purchases_draft.tt.txt` (never read until you rename it to end in `.tt`); a draft's `YYYY-MM-DD` date or `COST` total is a placeholder the reader refuses until you fill it in. `type=stock_dividend` marks the line as the shares a stock dividend paid (QTY positive, price and total usually 0), with the broker's ex-dividend (`ex=`) or record (`record=`) date when it gave one: the gains run applies the country's rule to them (US: the old shares' basis is spread over the new ones, which keep their dates, tax-logic US-STKDIV-01 to US-STKDIV-05; Canada: $0 until an `ADJUST` line gives the declared amount; in either country, posted while the position is short, the short grows by the new shares with its proceeds unchanged, nothing is covered or acquired, the loss rules read it so too, and an ATTENTION line says so, US-STKDIV-06, CA-STKDIV-02). Without it the line is a purchase at its total. The IB, Questrade and RBC Direct parsers mark the row; `taxjson-convert-tt` and `tjs events` write the tokens. On a sale, an ASSIGN line, an option or a future it is refused, as is a `type=` other than `stock_dividend`, `futures_settlement` or `trade_cancel` (the second example: 10 shares paid on a record date of 2025-02-28). `type=trade_cancel` marks the line as a broker's cancellation of a trade (IB code `Ca`): the original's reverse, the same symbol, date, price and opposite QTY. The run drops it with the trade it cancels, from any input of the account, and warns about one whose original is in none of them (it stays booked as a reversing trade). The IB parser marks the row; `taxjson-convert-tt` and `tjs events` write the token. `type=futures_settlement` is what `taxjson-convert-tt` writes for a futures contract's row of a book already put on the settlement basis (`F:` or `/` symbols only): its TOTAL is the realized P/L (0 on an opening, negative for a loss), not qty × price, so it is not compared; `settle=` carries the native facts the settlement recorded (the leg `LO`, `LC`, `SO` or `SC` — a long or short opened or closed, agreeing with the QTY's sign —, the native money, the P/L, the currency and the rate), which the Canadian engine re-averages a contract held in several accounts from and keeps a short's pool apart with. A book of such lines is settled once: beside raw fills of the same contract in one account it is refused. A settled line whose buying leg (`LO`, `SC`) records negative money at a price above 0 was written by an older `taxjson-convert-tt` from a buy in the cash sign and carries the wrong gain: it is refused, naming the line (convert the source book again through `tjs run`).
- **Cash in the FX-on-cash ledgers** (`tjs fx-cash`; the tokens change nothing else, and are no part of the row's id: a line with one and the same line without it are one row): without `cash_leg=`, a BUYSELL or ASSIGN line moved its TOTAL in the line's currency on the line's date (a buy paid it, a sale received it); a line with `corp=` moved none but its `cash=`. `cash_leg=none`: the TOTAL is a value and no cash moved (a crypto-for-crypto leg, a fee an exchange took in a coin). `cash_leg=reward`: the coins a reward paid (BUYSELL only; its income is the DIVIDEND line): no cash, unless SYMBOL is itself cash (a fiat currency, or a US-dollar stablecoin where the project counts one as cash, tax-logic CA-CRYPTO-02). `cash_leg=cash`, on a line with `corp=` and no `cash=` only: the line is the event's own cash leg and moved its TOTAL (a `corp=` line with `cash=` moved that cash alone: `cash_leg=cash` beside `cash=` is refused, naming the line). `refunds=`: the commission refunds the TOTAL is net of (IB's Commission Adjustments, tax-logic CA-ACB-COMMREFUND), each `DATE:AMOUNT` in the line's currency, joined by `;`: ledger v2 then walks the commission paid in full on the line's date (a buy's TOTAL plus the refunds) and each refund received on its own date; without the token the net TOTAL moves on the line's date (ledger v1 reads the net TOTAL either way). `taxjson-convert-tt` and `tjs events` write both from what the broker parser's row says (its wording, its source file, its refunds); add one to a hand-written line where the line means it. A value that is not one of these, a token on a line that does not take it, or a refund that is not `DATE:AMOUNT` with a real date and an amount other than 0, is refused, naming the line (the third example: 1 SOL given for another coin; the fourth: a purchase whose 1.00 commission was refunded a month later).
- **Example:** `BUYSELL 2025-03-10 09:30:00 ZZQ.US 10 USD 45.00 459.95 9.95`; `BUYSELL 2025-03-03 10:00:00 ZZQ.US 10 USD 0 0 type=stock_dividend record=2025-02-28`; `BUYSELL 2025-03-03 10:00:00 SOL -1 USD 100 100 0 cash_leg=none`; `BUYSELL 2025-01-13 09:30:00 ZZQ.US 10 USD 10 100 0 refunds=2025-02-14:1.00`

#### `ASSIGN`
- **Form:** as BUYSELL; a warrant's or a right's exercise leg ends with `exercise_of=SHARES`: `ASSIGN DATE TIME SYMBOL QTY CUR PRICE TOTAL [FEE] [xSIZE] [exercise_of=SHARES]`.
- **Meaning:** the two legs of an exercise or assignment, one line each: the option leg at price 0 and the stock leg at the strike; the premium folds into the shares' cost or proceeds (the first example: a written call assigned on 1 contract). A warrant or a right exercised is its leg with `exercise_of=` naming the share listing it delivers (QTY negative, the warrants given up; `xSIZE` the shares per warrant, when the broker declares it) and the shares' BUYSELL line at the exercise price: no disposition, the warrants' cost goes into the shares' cost (tax-logic CA-OPT-09 / US-OPT-06). Without the token the leg is a sale of the warrants at 0, a capital loss of their cost. The IB and RBC Direct parsers mark the leg; `taxjson-convert-tt` and `tjs events` write the token. Only an ASSIGN of a warrant or a right takes it: on a BUYSELL line, an option's leg, a positive QTY or its own symbol it is refused (the second example: a warrant exercised into 100 shares at 11.50).
- **Example:** `ASSIGN 2025-06-20 16:00:00 ZZQ250620C00005000.US 1 USD 0 0` then `ASSIGN 2025-06-20 16:00:01 ZZQ.US -100 USD 5 500`; `ASSIGN 2025-05-02 16:20:00 ZZQW.US -100 USD 0 0 exercise_of=ZZQ.US` then `BUYSELL 2025-05-02 16:20:00 ZZQ.US 100 USD 11.50 1150.00`

#### `TRANSFER`
- **Form:** `TRANSFER DATE TIME SYMBOL QTY CUR PRICE TOTAL [DECLARED]`
- **Meaning:** shares moved in (positive QTY) or out (negative). `DECLARED` marks a deliberate statement (a custody-move counter-leg), which the transfer checks accept; it is opt-in, so an ordinary `.tt` TRANSFER line never gains that weight by accident. Two DECLARED lines of one symbol, `N` and `-N` at price 0 and total 0, declare broker churn that nets to zero (what `transfers_as_acquisitions = true` asks for; the run prints the exact lines).
- **Example:** `TRANSFER 2025-02-03 09:30:00 ZZQ.TO -20 CAD 30.00 600.00 DECLARED`; a zero-net pair: `TRANSFER 2025-04-22 09:30:00 ZZQ.TO 50 CAD 0.0 0.0 DECLARED` and the same line with `-50`

#### `ACQUIRED` (shorthand)
- **Form:** `ACQUIRED TRUE-DATE TIME SYMBOL QTY CUR PRICE TOTAL ARRIVED ARRIVAL-DATE`
- **Meaning:** shares that arrived by transfer, with their real purchase: it expands to a BUYSELL on the true date plus a DECLARED counter-TRANSFER on the arrival date.
- **Example:** `ACQUIRED 2019-05-10 09:30:00 ZZQ.TO 40 CAD 12.00 480.00 ARRIVED 2024-06-03`

#### `OPENING`
- **Form:** `OPENING SNAPSHOT-DATE SYMBOL QTY CUR TOTAL-COST [LOT-DATE] [xSIZE]` (no time column; `xSIZE`: an option's contract size other than 100, such as `x10` for a mini option)
- **Meaning:** an opening balance from a positions report: sets the position and its book cost (never its market value) on the snapshot day, is not a purchase (never a superficial-loss or wash-sale replacement, never a planning tool's recent buy), and replaces the account's earlier rows of that symbol (trades, transfers, renames and cost adjustments dated on or before the snapshot day; income rows stay; a left-out sale of the tax year stops the run). Long positions only: a written option or a futures contract is a BUYSELL line. Canada: a foreign-currency cost is converted at the snapshot day's rate. A US line is one lot: it needs its lot date and a USD cost. `tjs opening` writes these lines.
- **Example:** `OPENING 2023-12-29 ZZB.TO 20 CAD 204.95`

#### `OPENING ... cost=unknown`
- **Form:** `OPENING DATE SYMBOL QTY cost=unknown [reason="TEXT"]` (no time column; the reason, in double quotes, is yours and may hold a `#`).
- **Meaning:** QTY units of SYMBOL held on DATE, bought before your files start at a cost they do not give — missing history, as a dated event. Sales that draw on them have an unknown cost: listed for manual reporting and left out of the totals (Canada: with no superficial-loss test, until the position is fully sold; US: FIFO, the opening lot first). The line's quantity and date are the opening, exactly: never sized from the rows, no tax-year window. The line is a position row like any other from the merge on: an opening snapshot of the symbol (`OPENING DATE SYMBOL QTY CUR COST`, or of the symbol a rename made it) dated on or after it replaces it as it replaces earlier purchases (nothing is opened at an unknown cost; an `ATTENTION: opening:` line names the line: delete it, or keep it dated after the snapshot only for units that came after it; `tjs find-missing-history` lists it under REPLACED and the checklist's missing-history step asks for the deletion; it answers for no later sale and no transfer-in), and the run stops when the inputs disagree: one pass would replace the line and another open it, because it reaches the snapshot only through a rename in another account's rows (the refusal names the line, both readings and the remedy: add the `RENAME DATE OLD NEW` line it prints to this account's .tt file, or delete the line), or its units and the rows before it hold more than the snapshot (a buy of the tax year would read as closing a short: correct or delete the line, or correct the snapshot). A dated `RENAME` on or after its date moves its units to the new symbol at their unknown cost (`tjs renames` shows them, `cost_unknown` in `--json`). Date it before the first sale it covers (the day before the account's first row is always early enough); a position already short before the date, or short again once the units are used up, is an ATTENTION line naming the line. A line naming a ticker's new symbol, dated before the change, opens the old symbol (the units the account held that day); one naming the old symbol, dated after the change, opens the new one only when the account folds the change's late rows (`late=fold`), as its rows are booked. The symbol goes through ticker.map and tobase.map as a row's does: the base-currency books open the TOBASE target, the native holdings view the listing the books trade; two lines that become one security open its units together when they have one date (on two dates the run stops). The units are held even when no later row trades them (a holding kept, or one that only pays income); when no row at all has the symbol a note says so (check the spelling). One line per symbol per account (two stop the run). In the shared layout (`inputs_dir`) the line is read by every year's project. `tjs find-missing-history --write-missing-history` writes these lines into `inputs/<account>/missing_history.tt` — dated the day before the account's first row, the quantity the units the run opens — merged with the lines the account's .tt files have: never written twice; a symbol a line already opens with another quantity is a Warning and the line is left as it is. It writes only positions that go short through the tax year (`--all-history`: every year; `--outside-year`: only those with no row in the tax year), never $0-cost corporate-action shares (they need a merger or spin-off cost) and never a journal's own days. A line the detection would not propose (a short the broker marks as one: RBC `SHORT.`, IB code `O`) is still applied, with an ATTENTION line, and listed under REMOVE; a line whose position no longer goes short without it (its purchase is in the books now) still opens its units at an unknown cost on top of the purchase, so the sales that draw on them stay out of the totals: every run says so as an ATTENTION line ("… lists SYMBOL / ACCOUNT, but its rows never go short any more"), `find-missing-history` lists it under STALE and the checklist step needs attention — delete it. It is never a row of the books: `tjs run` and every command that applies missing history read it (`src/taxjson/lib/missing_history.py` — `read_tt_openings`, `load_missing_history`, `synthesize_openings`; the parser is `parse_unknown_opening_line`). Both countries (tax-logic `CA-ACB-11` / `US-BASIS-04`). This line is the only form of missing history: the project-root `missing_history.json` (and its older name `phantoms.json`) of taxjson before v0.27.0 is no longer read — a project that still has one is refused, naming `tjs migrate`, which converts it: each entry becomes a line with the units that project's run opened for it (sized through its year's end, or its recorded `quantity`), dated the day before the account's first row; in a year folder of the shared layout it merges every year folder's file into one record (entries the years size the same are one line; entries they disagree on — another quantity, listed in only some years — are listed and written only with `tjs migrate --write`, each as the newest year listing it decides it — an explicit "opens nothing" there writes no line, a year whose books have no row of it does not decide; entries that open nothing in any year are dropped, said), then renames each file `missing_history.json.migrated` (`--dry-run` shows the plan). Each line is dated from the books of the year that sized it; an entry no year's books can date stops the migration with nothing written or renamed. The converter is `plan_missing_history_migration`.
  A negative QTY declares a short sold before your files start, its proceeds unknown: the buys that close it are listed for manual reporting and add no long, and a later sale of known purchases keeps its known cost (not on an option or a future). Date it before the cover. `--write-missing-history` drafts it, with the same date rule, for a buy the broker codes as closing (IB code `C`, RBC `COVER SHORT.`) with no short held, through the tax year; until the line or the short sale is added, the run and the `unbacked-cover` readiness issue flag the buy, which the books carry as a new long. A short line is never listed as stale; if you later add the short sale itself, delete the line. A symbol that needs both a long line and a short line cannot be written: add the older rows instead.
- **Example:** `OPENING 2021-12-31 ZZQ.US 10 cost=unknown reason="bought at the old broker in 2015"`; `OPENING 2024-12-31 ZZQ.US -10 cost=unknown reason="short sold at the old broker"`

#### `INKIND`
- **Form:** `INKIND DATE SYMBOL QTY CUR PRICE [TOTAL] [plan=KIND]` (no time column), in a taxable account's folder; or `INKIND DATE SYMBOL QTY plan=own` (no value).
- **Meaning:** declares and values an in-kind move between this taxable account and a registered plan (an RRSP or TFSA contribution, a withdrawal; US: an IRA distribution). QTY is signed as the shares move in this account: negative = out to the plan, positive = back from it. PRICE is the value per share in CUR; give `0` and a TOTAL to state the whole value. The line names this account's transfer row of that security and quantity nearest DATE (within 10 days) and books it as in kind, dated DATE: with the plan's transfer row of the same quantity (it settles an ambiguous pair the run would not book on its own; `plan=` picks the plan's leg), else the plan's rows that add up to it (a delivery in parts), else as a move to a plan outside the project — `plan=` names that plan (`rrsp`, `tfsa`, `ira` ...). `plan=own` declares the row a move of your own: never in kind, and it no longer makes another pair ambiguous or a delivery in parts suspect. `plan=` needs a value; a value that is not a finite amount is refused. It is never a row of the books; a line matching no transfer row is listed in the run's in-kind warning. The parser is `parse_inkind_line`.
- **Example:** `INKIND 2025-03-14 ZZQ.TO -100 CAD 15.00` (100 shares contributed at 15.00 each); `INKIND 2025-03-14 ZZQ.TO -100 plan=own` (that transfer-out was a move between your own accounts)

#### `NOTOWN`
- **Form:** `NOTOWN DATE SYMBOL QTY FROM-ACCOUNT FROM-DATE` (no time column), in the folder of the TAXABLE account the shares arrived in.
- **Meaning:** the transfer-in of QTY units of SYMBOL on DATE in this account and the transfer-out of the same units from account FROM-ACCOUNT on FROM-DATE are NOT one move of your own, though `tjs run` paired them as one: two rows of the same quantity within 10 days of each other, or a slower pair (the in row 11 to 45 days after the out row, neither with another candidate), which the run says as `Info: Own-account move: … nothing else pairs`, its detail (`tjs run --details`) printing this line to add. The line undoes that one pairing and never makes one: the transfer-in is then booked as any transfer-in from outside your books (at the book value the broker states on the row, else kept out with no cost, said as ATTENTION; a `.tt` purchase dated on or before it covers it), and the transfer-out is an ordinary one (the sending account's books keep the shares, as for any transfer-out); in a US project no lots move between the two accounts. Every other transfer pairs as without the line. Write the dates, units and accounts as `tjs transfers` lists the two rows, the symbol as the row spells it or as ticker.map renames it; QTY is positive. A line that undoes no pairing (another date or quantity, a pairing of two different quantities, a listing journal, a line in an account whose transfer rows are never paired: sheltered, crypto or `transfers = true`) changes nothing and is a Warning on every run, naming it; `tjs check-tt` also flags one in such an account or naming an account taxjson.toml does not declare. It is never a row of the books. There is no line to force a pairing. Both countries. The parser is `src/taxjson/lib/transfer_in.py` — `NotOwn`, `read_not_own`; `src/taxjson/lib/tt_format.py` — `parse_not_own_line`.
- **Example:** `NOTOWN 2024-03-16 ZZQ.TO 100 qa 2024-03-01` (the 100 ZZQ.TO that arrived in this account on 2024-03-16 did not come from account qa's transfer-out of 2024-03-01)

#### `JOURNAL`
- **Form:** `JOURNAL DATE FROM TO QTY [separate]` (no time column)
- **Meaning:** QTY units moved from listing FROM to listing TO of one security inside this account (a Norbert's gambit's journal, a TSX line moved to its NYSE line). Booked as the move's two transfer legs, kept with the account's transfer evidence (`work/<account>_tt-journal_transfers.json`), never a purchase or a sale: the two listings are joined as one security (as a ticker.map `TOBASE` line would; the base-currency listing is kept) when something shows they are listings of one security — one root (`ZZG.TO`, `ZZG.U.TO`, `ZZG.US`; a class or unit designator is part of the root; a depositary receipt — written on a venue `src/taxjson/data/markets.toml` marks `receipts = true`, a CDR on Cboe Canada such as `ZZG.NE`, or named as a receipt in the exports, `[lists] receipt_words`: CDR, ADR — is its own security, never one root with the share) or names in the exports that agree as a broker journal's legs' names must, and no names of two different companies; otherwise the run stops, and a ticker.map `TOBASE FROM TO` line is the deliberate join (the line is then booked). The legs move units inside this account only (never paired with another account's transfer); the holdings view moves the units, the missing-history checks read the day as a journal. Both countries. When the broker's rows already hold both legs (the account's out-leg of FROM and in-leg of TO, the same quantity, within 5 business days) the line is a duplicate: an Info line, nothing booked twice; with one leg there, only the other leg is booked. A ticker.map rule naming either listing wins; a `DISTINCT` line keeps them apart (a Warning). Refused in a crypto account, for an option contract or a future (never journaled into shares), for a date in the future and for an implausible quantity (over a trillion units). Two identical lines (account, date, FROM, TO, quantity) are one journal, with a Warning: two journals of one size on one day are one line with the total. A line that moves MORE units than a journal the broker's rows already hold between the same two listings on the line's date stops the run: write only the units the rows lack. A line on another date is another journal, booked. One a business day from the broker's journal (or on its trades' day) moving more units is booked and said as a Warning (a likely restatement); a line ending `separate` is a journal of its own: booked in full, never settled against the broker's legs near it (not even one of the same size), never said as a restatement.
- **Example:** `JOURNAL 2025-03-05 ZZG.TO ZZG.U.TO 100`

#### `RENAME`
- **Form:** `RENAME DATE OLD NEW [late=fold|late=separate]` (no time column)
- **Meaning:** a ticker change on that date, booked as an event: the pool (US: the lots and holding periods) carries from OLD to NEW. Written in any account's .tt file, it applies to every account of the same kind (securities or crypto: a crypto account's line is a coin's ticker change) whose books hold OLD before the date — or a symbol an earlier change moved into OLD, or an option on OLD, or units an `OPENING ... cost=unknown` line opens on or before the date (they move to NEW at their unknown cost) — and is recorded once. An option follows its underlying's line: each contract on OLD an account still holds at the change (bought or written, not expired before it) moves that day to the same expiry, right, strike and market on NEW, premium and dates carried; a contract with a rename row of its own within a week (a broker's adjustment, a `.tt` SPLIT of the contract) is left to that row. An option counts as holding OLD only while a contract is open at the date: an account whose contracts on OLD were closed or expired before it does not carry the event or follow its `late=`. While units the change moved are left in the account (bought or written), every row after the change under that contract's OLD symbol is the same contract the broker still names by its old root: it is booked as NEW whatever `late=` says — a close (a sale or buy-to-close, an expiry, an assignment or exercise with its share leg), an addition, and a close of more than is left, whose excess opens a written (or bought) position on NEW with a Warning naming the row and the excess. A note in the account's diagnostics names the rows and `tjs renames` lists them under the contract's event. Only the units the event moved count, with the rows under OLD so booked: each close under either root takes from them and a purchase under NEW adds none, so once they are closed a row under the OLD symbol is a late row like a share's and follows `late=`, even when the account has opened the NEW contract again (`src/taxjson/lib/renames.py` — `_book_option_series`, `_fold_late_rows`, `_option_holders`). Every declaration of one change (`.tt` lines in several accounts, a legacy ticker.map line, within a week of each other) is one event dated the earliest (an Info line); the changes apply in date order. Refused, naming the lines: OLD renamed to another symbol within a week, one change on two dates further apart, a cycle (`A -> B` and `B -> A`), one account choosing both `late=` values. A trade in OLD after the change (later on its own day included) is listed by `tjs renames` and stops `run --strict` until `late=fold` (book it as NEW) or `late=separate` (another company reusing the ticker). A line's `late=` applies to its own account's late rows and to every account without a line of its own that held OLD before the date; another account's own line may choose otherwise for its rows. An account that did not hold OLD before the date (another company may use the ticker there now) keeps its OLD rows as OLD, listed as undeclared (an Info line says so), until a line of its own says `late=`. An account's late rows are the ones its country's engine takes after its own rename row: in Canada (settlement-date order) every OLD trade executed on the rename row's date, even earlier that day than an evening corporate-action row; in the US (trade date and clock time) an OLD row before its broker's rename row is still OLD. A chain `A -> B` then `B -> C` declared for one day carries the position twice, in the order of the lines (a broker's or a `.tt` SPLIT into B that day counts too). OLD may be the symbol ticker.map gives the rows (`GLOBAL RAW OLD`): the line books on the RAW rows too. A line no account's books carry (no account of its kind holds OLD before the date: a typo of the symbol, a date after the last OLD row) books nothing: a Warning names the line, `tjs renames` lists it under DECLARED, NOT BOOKED (`--pending` counts it), and `run --strict` stops. IB's one contract id under two symbols is booked as this event without a line (a Warning names the `DISTINCT` and `late=separate` way out) on the new symbol's earliest row of any section (a trade, a corporate action, a dividend, a return of capital) when every old-symbol row is on an earlier day; when the rows cannot date it (the two symbols' rows overlap, a symbol is listed under two contract ids in one statement) an ATTENTION line gives this line to add, with the date. A line for the change books it instead. A line dated in the future is refused, and so is one naming an option contract or a future: an option follows its underlying's ticker change (write the stock's line), and another expiry, strike or right is another contract. A line between two spellings of one listing in the books (`ZZA.TO` and `ZZA.CN`: a Canadian venue folds into `.TO`) books nothing, with an Info line.
- **Example:** `RENAME 2025-04-01 ZZOLD.US ZZNEW.US late=fold`

#### `ALLOWLOSS`
- **Form:** `ALLOWLOSS DATE SYMBOL [QTY] reason="TEXT"` (no time column; the reason is required, in double quotes, and may hold a `#`), in the folder of the TAXABLE account that sold.
- **Meaning:** a filing position against ONE superficial-loss denial (US: one wash-sale disallowance): that sale's loss stays allowed, and no ACB (US: basis) is raised for it on the replacement (no double benefit; US: no holding period carried either). DATE is the sale's trade or settlement date, SYMBOL its symbol as the books spell it (`tjs wash-sales` shows it), QTY the units sold — needed only when two denied sales of the symbol share the day; it is the sale's total (one same-day sell-down in several fills is one sale, and one fill's units name no sale). A `reason=` after a `#` is part of the comment, not the line's reason. The replacement units the denial would use stay used, so every other sale's verdict is unchanged. It is never a row of the books: `tjs run` reads every account's lines into `work/loss_overrides.json` and gives them to every engine run of the books (`--loss-overrides`). A line that names no denied sale, or two, stops the run naming it (with that day's trades in the account); a malformed line, a line in a sheltered account and two lines naming one sale are refused before the books are built (the same sale spelled twice — with and without the units, by trade and by settlement date — stops the run once the books name it). Every position is a Warning on every run and is listed in `tjs sum` (FILING POSITIONS; `filing_positions` in `--json`, with the denial the rule would make and why), `tjs wash-sales` (and each sale's `--explain` trace), `tjs audit`, the US `tjs wash-radar`, `tjs carryover`, `tjs handoff` and the checklist's filing-positions step, and noted on its row in `tjs form-export` (Schedule 3 notes; Form 8949: no code W, nothing in (g), a note under the part and in the CSV's `note` column; a TXF export warns, its record carries no wash-sale amount). It is your position, not the rule's test (tax-logic CA-SL-18 / US-WASH-25): delete the line to apply the rule. The parser is `src/taxjson/lib/loss_overrides.py` — `read_project`; `src/taxjson/lib/loss_override_note.py` — `parse_line`.
- **Example:** `ALLOWLOSS 2025-12-19 ZZQ.US reason="the RRSP call was bought 32 days after the trade date"`

#### `FXCONV`, `CASHMOVE`, `CASHOPEN`, `CASHBAL`, `CASHBOOK`
- **Form:** `FXCONV DATE FROM AMOUNT TO AMOUNT [value=BASE] [at=BOOK]`; `CASHMOVE DATE CUR SIGNED-AMOUNT HOW [at=BOOK] [row=FILE:LINE]` with HOW one of `own`, `kept`, `spot`, `cost=BASE`, `proceeds=BASE`; `CASHOPEN DATE CUR SIGNED-UNITS BASE-COST [at=BOOK]`; `CASHBAL DATE CUR BALANCE [at=BOOK]` (no time column); `CASHBOOK BOOK [complete]` (no date), in the folder of a TAXABLE account.
- **Meaning:** cash events for the FX-on-cash ledger v2 (`fx_cash_ledger = "v2"`), never rows of the books; `tjs run` only checks their form. `FXCONV`: a currency conversion no export carries (both amounts positive: what left, what arrived; `value=` the base-currency value when neither side is the base currency, else the day's rate prices it). `CASHMOVE`: cash in (+) or out (−) of an account. When a broker export carries the same move (same account folder, date, currency and amount) the line DECLARES what it was instead of adding one: `cost=` what money coming in from outside the books cost you (in the base currency), `spot` the day's rate, `own` a move between two of your accounts in the books (not a disposition: the units and their cost stay in the one pooled average cost while in transit, so a move that arrives days later gives the same figure; the out must come before the in, and on one settlement day an in booked on an earlier trade date waits for its out), `kept` money going out that you still hold in that currency elsewhere (it leaves at its cost, no gain), `proceeds=` what money going out fetched when converted or spent (a disposition). A crypto account's stablecoin send that `tjs crypto-sends` decided needs no line: the ledger takes the decision (`gift` or `payment`: `spot`; `self`: `kept`), and a line for it overrides the decision only on its own side (`own` or `kept` for `self`, `spot` or `proceeds=` for a gift or a payment); a line of the other side is refused with the decision, both named. A line written for a Kraken Hybrid Earn row (`hybridearnwithdrawal`, `hybridearndeposit`) declares cash that never moved and is refused in its year. `row=` names the broker's row the line declares, as the ledger's messages name it (`row=kr_ledgers.csv:6`; in double quotes where the file's name holds a space, a `#` or a quote, as a browser's second download does: `row="kraken_ledgers (1).csv:6"`, a `"` or a backslash of the name written after a backslash; where overlapping exports hold the row, its line in any of them); it is needed only where a line fits both a cash move and a Hybrid Earn row of one day and amount (the ledger then names every such line and both rows and takes none, and the line it asks for such a move, like the `?` line of `tjs cash`, carries its `row=`), a `row=` that names no undeclared move the line fits is refused, and one that is not `FILE:LINE` is a malformed line. `CASHOPEN`: an account's SETTLED balance at the start of the year, dated Jan 1, with its cost (0 or more, and 0 for 0 units; a negative balance is a debt; the cost is then what the borrowed units were worth when borrowed); `close-year` with ledger v2 records the next year's opening, so these lines are needed only for a first year (a recorded opening is held to the same rules: one that breaks them is a damaged record, and nothing is computed on it). `CASHBAL`: the balance a statement shows at the end of that day (trade-date basis) — the ledger reconciles to it; an account with no statement balance (RBC, Webull, Coinbase, a bank) needs one on or after its last event of the year. In a Canada project USD stablecoins are US-dollar cash (CA-CRYPTO-02), so a crypto account's `CASHBAL … USD` is its US dollars and its USDC (and other USD stablecoins) together, where the statement lists them apart. `at=` names the account's cash book as `tjs fx-cash --ledger v2` prints it (`ib`, `rbc:3f2a`, or any word for a book kept by hand such as `bank`); without it the line goes to the book the file's `CASHBOOK` line names, else to the folder's only broker account. `CASHBOOK`: whose cash the file's rows move — its trades, income and cash lines — in a folder holding several broker accounts (a Webull `.tt` beside IB and RBC exports: `CASHBOOK webull`); the ledger never guesses it from the file's name, and a file whose rows are several accounts' is split. `complete` after the book says its `.tt` cash lines are ALL of its conversions, deposits and withdrawals — needed for the book of a broker whose export the ledger reads none from (Webull, the generic importer) once it moves foreign cash. A malformed cash line stops `tjs run` whichever ledger is in use; the message says it is read only by ledger v2. A row of a broker statement dated before the statement's period (a correction posted with the original date) moved its cash inside the period, and is walked at the period's first day. The parser is `src/taxjson/lib/cash_lines.py` — `parse_line`, `FORMS`.
- **Example:** `CASHMOVE 2025-03-03 USD 5000 cost=6850.00` (a deposit the IB statement shows, declared); `CASHMOVE 2025-06-02 USD -2000 kept` (withdrawn to your own US-dollar bank account); `CASHOPEN 2025-01-01 USD 1200.00 1610.40 at=ib`; `CASHBAL 2025-12-31 USD 340.12 at=webull`; `CASHBOOK webull`; `CASHBOOK webull complete`

#### `SPLIT`
- **Form:** `SPLIT DATE TIME OLD NEW RATIO [corp=EVENT election=NAME]`
- **Meaning:** a split or consolidation (RATIO new shares per old: `2`, or `0.1` for one-for-ten) or, with RATIO `1` and a new symbol, a dated rename. `corp=` and `election=` name the corporate action that made the row (`taxjson-convert-tt` and `tjs events` write them; the event is part of the row's id, so the line reads back to the same row; any other `key=value` on a SPLIT line is refused). A new symbol other than OLD is part of the row's id; two SPLIT rows of one account that rename OLD on one day to two different symbols (two exports that disagree, a line beside the broker's row) are refused, both named with their files: a rename moves the whole holding to one symbol, so keep the right one.
- **Example:** `SPLIT 2025-05-01 09:30:00 ZZOLD.US ZZNEW.US 1`

#### `ADJUST`
- **Form:** `ADJUST DATE TIME SYMBOL CUR AMOUNT [type=roc|dist] [record=YYYY-MM-DD] [cash_leg=cash] [corp=EVENT election=NAME parent_qty=N cash=AMOUNT:CUR]`
- **Corporate-action provenance:** on a trade, income or `ADJUST` line, `corp=` and `election=` name the event and election that made the row, `parent_qty=` (an `ADJUST` only) the parent shares a spin-off was paid on, and `cash=` the cash the event paid (cash in lieu, boot: `7.5:USD`, a later payment `7.5:USD:@2025-06-05`, joined by `;`), which `tjs fx-cash` counts. `taxjson-convert-tt` writes them when it turns the books into a `.tt` file, so the file books the same figures; you do not type them by hand. The event (`corp=`) is part of the row's id on every line that takes the token (a trade, income, ADJUST or SPLIT line), so two events' rows that are otherwise equal (two spin-offs of one parent on one day with one allocated amount) are two rows; `election=`, `parent_qty=` and `cash=` are not.
- **Meaning:** a cost adjustment, five fields after the action (not the BUYSELL shape): negative lowers the ACB/basis (a return of capital, T3 box 42), positive raises it (a stock dividend's declared amount, a notional distribution). On a short position it changes the cover's gain. `type=roc` lists it as a return of capital (`tjs roc`, `tjs roc-sum`); `record=` is the record date that dates a trust's return of capital. A positive ADJUST of a symbol in the account dated from 31 days before to 7 days after $0-cost shares arrived gives them a cost, and `tjs find-missing-history` stops listing them. Enter a fund's return of capital as ADJUST lines or as `[[distributions]]`, never both. In the FX-on-cash ledgers an ADJUST line moves no cash (a T3's box 42 entered by hand: its cash came with the distribution's own DIVIDEND line). `type=roc cash_leg=cash` says the return of capital was itself paid in cash: the ledgers receive it (AMOUNT with its sign turned) in the line's currency on the line's date. A broker's return-of-capital row is one, and `taxjson-convert-tt` and `tjs events` write it this way; `cash_leg=cash` without `type=roc`, or any other `cash_leg=` value on an ADJUST line, is refused (the second example: 10.00 US dollars of return of capital a broker paid).
- **Example:** `ADJUST 2025-12-31 16:00:00 ZZF.TO CAD -12.50 type=roc` (T3 box 42 return of capital); `ADJUST 2025-05-05 16:00:00 ZZF.US USD -10.00 type=roc cash_leg=cash`

#### `DISALLOW`
- **Form:** `ADJUST`'s shape.
- **Meaning:** a recorded loss disallowance, as `taxjson-convert-tt` writes it when converting a book that has one; rarely written by hand.

#### `DIVIDEND`, `DIVIDEND_IN_LIEU`, `TAX`
- **Form:** `ACTION DATE TIME SYMBOL QTY CUR PRICE GROSS [NET] [facts]`
- **Meaning:** income (or withholding, TAX). Facts are optional `key=value` tokens at the end: `record=` (the record date that dates trust income), `ex=`, `label=distribution`, `dealer=CA` / `issuer=CA` (a Canadian dealer's payment in lieu on a Canadian share is a deemed dividend in Canada), `label=return_of_capital` on a DIVIDEND_IN_LIEU line (the payment it replaces was a return of capital: ordinary income, no deemed dividend). In the FX-on-cash ledgers the line's GROSS comes in (a TAX line's amount goes out) in the line's currency on the line's date; a line with `corp=` moves none but its `cash=`. `cash_leg=none` says no cash moved (the income was paid in kind); `cash_leg=reward`, on a DIVIDEND line, that it is a reward paid in SYMBOL: no cash, unless SYMBOL is itself cash (a fiat currency, or a US-dollar stablecoin where the project counts one as cash), and then, when the line is valued in another currency, QTY units of that cash come in (5 USDC valued in CAD: 5 US dollars); `cash_leg=cash`, on a line with `corp=` and no `cash=` only, that the line is the event's own cash. `taxjson-convert-tt` and `tjs events` write the token for a reward's rows.
- **Example:** `DIVIDEND 2025-01-06 09:30:00 ZZF.TO 100 CAD 0 25.00 record=2024-12-30 label=distribution`

#### `INTEREST`, `FEE`
- **Form:** `ACTION DATE TIME CUR AMOUNT`; an INTEREST line may end with `symbol=SYMBOL`.
- **Meaning:** income or a charge with no symbol (an interest figure a trade export lacks). A FEE charge is positive, a rebate negative. `symbol=` names the property an INTEREST line is paid on (a foreign bond's coupon): its T1135 row's income (tax-logic CA-RPT-14); without it the interest is the account's cash's. `taxjson-convert-tt` and `tjs events` write it. Like every income line, a `taxjson-convert-tt` export dates a dividend, tax, interest or fee line the day it was paid, on either `--date-basis`. Neither line takes `cash_leg=`: in the FX-on-cash ledgers its amount always moved as cash, in the line's currency on its date.
- **Example:** `INTEREST 2025-12-31 16:00:00 USD 12.40`; `INTEREST 2025-06-30 16:00:00 USD 25.00 symbol=ZZB.US`

### Duplicate rows across files

Overlapping exports of one account (a re-download, a 2025 export that runs into January beside the 2026 one) hold the same rows twice; the books keep each row once. The rule, shared by the books and the fees report (`src/taxjson/bin/taxjson_sort.py` — `DedupPlan`):

- every parser that sees the broker account (IB's statement account, Questrade's `Account #`, RBC's `Account`, Webull's preamble, a generic mapping's `account` column or `[broker] account`) stamps each row with it, hashed: rows of two different broker accounts are never one row;
- two identical rows in one file are one row, unless the parser marked them as separate fills (`[fill #2]`); a row's id holds the corporate event that made it and a rename's destination, so the rows of two corporate events are never identical, and two renames of one holding on one day to different symbols are refused, both named;
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
| `[columns]` | `date` (required, the trade date), `settle`, `action`, `symbol`, `symbol_new` (a `rename` row's new ticker), `ratio` (a `split` or `rename` row's ratio), `book_value` (a transfer row's stated book value), `market_value` (a transfer's value at transfer; both may name the `amount` header), `lot_date` (an `opening` row's acquisition date, in `[formats].date`, its date part or `YYYY-MM-DD`), `quantity`, `price`, `amount` (fee-inclusive signed total), `fee`, `currency`, `account` (the broker account of the row; wins over `[broker] account`), `account_type` (the broker's plan type of the row's account, such as TFSA or Non-registered: checked against the taxjson account's `type` as a Questrade export's Account Type is, over each file and over all of the account's files: a mismatch an ATTENTION line, several accounts an ATTENTION line (a note with `combined_broker_accounts = true`), refused when one is a registered plan and the taxjson account is taxable; a type naming neither a plan nor margin, cash or non-registered is a note, the check unable to judge it; a row whose `account` cell is blank names no account), `description` (the broker's text for the row: the booked row's description when mapped and non-blank, what `[[classify]]` rules and `[extract]` read; the description is part of a row's id, so mapping it to an existing project changes the ids; the run's checks read it as they read a broker parser's text: an event word such as DRIP or MERGER skips the settle-date check, `ON n SHS` in a dividend states a holding, and it is the security's name in the listings) | CSV header names, case-insensitive; two fields may not share one header; the header is the first of the file's first 20 rows holding every mapped name (a blank line is a row; a quoted cell over two lines is one), so a title, an account line or a blank line above it is read past (a message names the file's own line; `tjs redact` blanks those lines whole) |
| `[formats]` | `date`, `settle` (strptime, default `%Y-%m-%d`; `settle` defaults to `date`'s format), `tax_sign` (`cash` or `withheld`), `fee_sign` (`cash`: a charge is negative cash, the default; `charged`: positive = charged), `option_expiry` (strptime naming a year, a month and a day, checked at load; default `%m/%d/%y`: the expiry an `[extract] option` pattern reads) | how cells are written; quoted strings |
| `[actions]` | CSV value = `buy`, `sell`, `dividend`, `dividend_in_lieu` (a payment in lieu: ordinary income, never a dividend), `tax`, `interest`, `fee`, `split` (the `ratio` cell is the ratio, new units per old unit: 2 for a 2-for-1 split, 0.1 for a 1-for-10 consolidation; the pool's cost and acquisition dates carry; the quantity cell is never the ratio, and a row carrying an amount or fee is refused: a cash-in-lieu sale is a .tt line), `rename` (the `symbol_new` cell names the new ticker; the `ratio` cell the ratio, 1 when blank; a rename to itself books nothing; a split row whose `symbol_new` cell names a new ticker renames too; a ratio of a thousand or more is refused as a share count), `assign_buy`, `assign_sell` (one leg of an exercise or an assignment, the contracts or shares in or out, booked as an ASSIGN row: the option leg at price 0, its amount 0 or its fee alone, and the shares' leg at the strike, as a buy's or a sell's money; the engines pair the two legs and fold the premium into the shares' cost or proceeds, tax-logic CA-OPT-06 / US-OPT-02, whichever leg comes first; the importer pairs them in the file as the engines do (a marked leg, or a trade at the strike), and the option leg settles on its share leg's day, CA-DATE-04; a written call assigned is the call's `assign_buy` and the shares' `assign_sell`, a written put's both `assign_buy`, a held call exercised the call's `assign_sell` and the shares' `assign_buy`, a held put exercised both `assign_sell`; an option leg with a price or an amount beyond its fee, an option leg whose side contradicts the share leg beside it, a share leg at $0 (its price 0, or its amount its fee alone), and a futures contract or its option, are refused; one label for both legs is told apart by a `[[classify]]` rule; a warrant's or a right's exercise stays a `.tt` line with `exercise_of=`), `transfer_in`, `transfer_out` (an in-kind move; the quantity cell the units, its sign may agree with the direction, never contradict it; the amount and price cells are not read on a transfer (a note counts the amounts left unread); a `book_value` cell is the broker's stated book value (the cost evidence of a transfer in), a `market_value` cell the value at transfer, and either may name the amount column's own header; the pipeline nets cross-account pairs and books the rest as transfers in or out), `opening` (a position held at the start of the data with a known cost, as a .tt `OPENING` line: the quantity cell the units, long only; the amount cell the total cost; a `lot_date` cell the acquisition date, without which the holding period starts at the snapshot day; an option's contract size the ticker.map `MULT` line's; booked at 00:00:00 of its day), `roc` (a return of capital: the amount cell is the cash received, signed as the export signs cash, taken off the pool's cost, a broker's reversal row netting it out; past the cost a deemed gain), `adjust` (a cost adjustment: the amount cell as signed), `cash` (a deposit, withdrawal or other cash move: books nothing and is listed as a recognized non-event; a `cash` row carrying a quantity, a ticker or a ratio is an UNBOOKED warning of its own, as shares moved in kind are not a cash move: a .tt TRANSFER line, or a `transfer_in` / `transfer_out` mapping, books them) or `skip`; an event row carrying a fee is refused (a fee is a fee row), as is a `roc` or `adjust` row with units or a price, an `opening` on a futures contract, and an `opening` whose amount is not its units times its price when a price is mapped | the action map, matched ignoring case and surrounding spaces (two keys alike that way with different values are refused); unmapped values are counted, and one carrying a quantity or an amount is an UNBOOKED warning (refused by `run --strict`, failed by `taxjson-brokerage --lint`): map it, or map it to `skip` |
| `[defaults]` | `currency`, `action`, `symbol` | values for columns the CSV lacks (never a cell a cut-off file lost); quoted strings |
| `[options]` | `allow_large_fees` (a fee over 5% of the gross is refused unless true), `settle_on_trade_date` (the date column is already the settle date: every trade settles on it) | switches |
| `[broker]` | `name` (the real broker: letters, digits, `-`, `_`; lower-cased), `account` (the broker account every row belongs to, a quoted string; stored hashed), `verified` (`false`: the layout was never run on a real export, as the shipped Wealthsimple one; `true` or absent: nothing is said) | labels. With `verified = false`, `tjs run` warns once per account that reads the layout, in its end-of-run list too, to check the account's totals against the statements; a mapping that names a `[layout]` cannot set it. With a `name`, `tjs run` parses that broker's files on their own (`work/<account>_generic-<name>.json`) and the fees report gives it its own row (`generic:<name>`); without one every generic file is one `generic` row. The account keeps identical rows of two broker accounts apart in the duplicate check |
| `[signature]` | `headers` (two or more header names, quoted strings; case and surrounding spaces ignored) | the mapping reads any CSV whose header (the first of its first 20 rows to hold them) has every listed name, under any file name: the folder's `generic.toml` for the files beside it, a layout (below) for every account; a file whose header also matches a supported export is refused as ambiguous, and a sidecar picks; a positions report the signature fits stays a positions report |
| `[layout]` | `name` (letters, digits, `-`, `_`) | the mapping stands for the named layout, `layouts/<name>.toml` in the project (the one `tjs run` runs in, its exports shared or its own) or in `~/.config/taxjson/layouts/` (`TAXJSON_LAYOUTS` names another folder; empty, none), else one taxjson ships (`src/taxjson/data/layouts/`: Wealthsimple's, [brokers.md](brokers.md#wealthsimple)); only `[broker]` may stand beside it, its keys winning over the layout's (`account` per account folder). A layout is a whole mapping, usually with a `[signature]`, and never names a layout; a bug report's `tjs redact` copy does not include it, so attach it. An edit to a layout rebuilds the parse like a mapping edit; a frozen year hashes the layouts its books are read with, and `tjs close-year` copies one from the user's folder, or a shipped one, into the project's `layouts/`. A shipped layout is weighed only when no project or user layout fits the file, and one of the same name replaces it (copy it there to edit it). A layout that cannot be read, or whose signature is invalid, stops the run naming it |
| `[[classify]]` | one table per rule, in order: `match` (a regular expression, searched case-insensitively; anchor it with `^` and `$` for a whole cell), `target` (any `[actions]` target), `over` (`action`, the default, or `description`), `action` (optional: a regular expression the row's action cell must also match, so a rule over the description reads only the rows of one action, such as `^Trade$`) | a row whose action value is not in `[actions]` takes the first rule whose `match` is found in the cell `over` names (and whose `action`, when given, is found in the action cell); `[actions]` always wins over the rules; a rule over the description needs `[columns].description`; refused at load: an invalid expression, an unknown target or key |
| `[extract]` | `symbol`, `symbol_new`, `quantity`, `ratio`, `price`, `option` = a regular expression with one capturing group (`ratio` may have two: new units, then old units: `2 FOR 1` is 2, `1 FOR 10` is 0.1), searched case-insensitively in the description cell | the group is the field's value, on a row whose target books the field (a trade or an opening: the quantity and price; a split: the ratio; a rename: the new ticker and the ratio; a transfer: the units; every row: the symbol, so `symbol` alone may stand for the symbol column), when the field's own column is unmapped or its cell blank (a mapped, non-blank cell wins); a row nothing maps takes every pattern for its UNBOOKED check; needs `[columns].description`; a value that is not a number is refused naming the row and its source; `option` names four groups instead, `(?P<right>...)` (C, P, CALL or PUT), `(?P<root>...)`, `(?P<expiry>...)` (in `[formats] option_expiry`) and `(?P<strike>...)`, and is searched in the symbol cell, then the description cell (either mapped): on a trade, an opening or a transfer row it matches, the contract's OCC symbol (`ZZQ250321C00005000`: the root upper-cased, a class share's `-` or `/` a dot; the listing suffix from the currency as for any symbol, or the symbol cell's own known listing, `ZZQ.US` in CAD, a class share's letter being none) is the row's symbol, so an option written as text is booked as the contract, not its underlying; read from the description beside a ticker in the symbol cell (the root's own ticker, or a prefix of an adjusted or a weekly root: another is refused), the row is the contract when its money reads as an option's (a price and an amount both non-zero: the price times the quantity times 100, a mini's 10 or the ticker.map `MULT` size, the fee aside) and the ticker's shares when it reads as theirs (an assignment's or an exercise's stock leg names its option too), and with no price and amount to tell (a blank or a 0), the contract only when the pattern matches the whole description (anchor it, `^...$`), else it is refused; with the pattern the mapping's one symbol source, a trade or a dividend row it does not match is refused; a symbol cell that is itself another contract's OCC symbol, a right, an expiry (outside 2000 to 2099 too) or a strike it cannot read, and a root no OCC symbol can begin with, are refused naming the row; a dividend or another row that books no contract is not read by it |

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

**Dates.** Map `date` to the trade date. A settle column (`settle`, with `[formats] settle` when its format differs) is used when filled; otherwise a trade settles on the standard cycle of its listing's market — T+1 from May 2024, T+2 before, T+3 before 2017-09-05, T+5 before 1995-06-07 in the US and Canada, options T+1, other markets on their own cycles (tax-logic CA-DATE-04 / US-DATE-04) — counted in that market's settlement days (the Canadian calendar for `.TO`, the US one for `.US`, the UK and ASX cycles for `.L` / `.AX`, else the row currency's), skipping weekends and holidays. With `tax_date = "settle"` a sale on Dec 31 therefore lands in January. A settle date before the trade date or more than 31 days after it is refused; more than 7 days after it is an ATTENTION line. Futures settle on the trade date (the next settlement day under `futures_settle = "next_day"`). An option closed at $0 on its expiry day is dated and settled that day, a commission charged on it included (a $0 close posted up to 7 days later is moved back to it). A `[formats] date` with a clock (`%Y-%m-%d %H:%M:%S`) gives each row its stated time (a trade, a split or rename, a transfer, a dividend, interest, tax and fee row), so a day's trades replay in time order whichever way the export lists them; without one, a row is stamped 09:30:00 and a day's rows keep the export's order (tax-logic CA-DATE-14 / US-DATE-13). Two kinds of row are not at their clock. An `opening` row is at 00:00:00, ahead of its day. A cost adjustment (a `roc` or `adjust` row) is stamped 09:30:00 whatever clock its cell carries, as every importer stamps a return of capital: the clock on such a row is when the broker posted it, not when the amount became payable. At 09:30:00 it comes after its day's rows stamped 09:30:00 or earlier (every trade, when the date format has no clock) and before a trade clocked later; in a Canadian project a trade takes its place on its settlement date (`tax_date = "settle"`, the default), so unless the mapping sets `settle_on_trade_date` the pay day's trades all come after it. Where the adjustment comes first, a sale that day is made at the changed cost. One that finds nothing held is, in Canada, a capital gain on an empty pool, with a warning; in a US project it is not applied, with an ATTENTION line. To place a cost adjustment at a time of your choosing, enter it as a `.tt` ADJUST line, which keeps the time written. Dividend, tax, interest and fee rows are dated `date`.

**Not covered.** An exercise or an assignment the export writes as a $0 option close beside a stock trade at the strike, with no label of its own, is an ATTENTION line: give the two rows' actions (or a `[[classify]]` rule) `assign_buy` / `assign_sell`, or map them to `skip` and enter both legs as `.tt` ASSIGN lines. A warrant's or a right's exercise is a `.tt` ASSIGN line with `exercise_of=`. The importer books securities only (every symbol gets a listing suffix), so it is not a route for crypto: a generic file in a `crypto = true` account is refused; coins go in a crypto account (the Kraken or Coinbase export, or `.tt` lines with the bare coin symbol).

---

## holdings/: the year's positions snapshots

The broker's positions files for the tax year, as a download tool writes them (one `[[holding]]` TOML per broker account; see [Holdings TOML](#holdings-toml)), or the broker's own report (an RBC Holdings Export, an IB Activity Statement with Open Positions; a `.csv`). `tjs sanity` with no arguments compares the books with them, without a `holdings = [...]` setting (`tjs run` does not; its closing list names `tjs sanity` while the folder holds one); an account that has one keeps its own files (a file it lists is never another account's, nor named as unclaimed). Code: `src/taxjson/lib/holdings_dir.py` — `discover`, `folder_for`, `listed_files`.

- A file belongs to the account whose `account` or `broker_accounts` holds the file's `[meta] broker_account` (the broker's id; `tjs fetch --positions` writes the taxjson account's name in `[meta] account`, which a renamed account no longer matches), else its `[meta] account`; else to the account its name starts with (`margin_holdings.toml`, `margin_ib_holdings.toml`; a broker's report has no `[meta]`, so its name claims it: `margin_holdings.csv`). A file no account claims, or two do, is named in a note and not compared. Several files of one account at one date are summed (one taxjson account across several broker accounts); at different dates, each broker account's snapshots (`[meta] broker_account`) are compared one date at a time, and files that do not name their broker account are refused (one broker account at two dates cannot be told from two broker accounts).
- A snapshot is compared at its date: `[meta] as_of`, else the day of `[meta] generated_at`. One dated after the books' last day is compared with the latest books, with a note.
- A snapshot with no `[[holding]]` table, only its `[meta]` (no `holdings_count` above 0), is an account that holds nothing: every position the books hold there is a difference.
- Keep a snapshot taken at (or just after) the year end in that year's folder: it is the year's closing positions. A later download belongs in the later year's `holdings/`.
- Any other file there (a PDF statement, a spreadsheet, an activity export) and a sub-folder are not read: `tjs sanity` names each ("is not read") and counts them on its `INCOMPLETE:` line. `README.txt` (which `tjs init` writes) is left alone.

## Holdings TOML

A broker's positions on a date, read by `tjs sanity` (and `[accounts.NAME] holdings`) and `tjs opening`; sanity compares the quantities of every account and the costs of TAXABLE accounts only (a sheltered account's book cost — RRSP, TFSA, IRA, 401(k), HSA — is not a tax cost: one `Info:` line says so; `accounts_not_compared` in `--json`), and lists a dividend stating more shares than the books held on its record date (settled that day, or traded by the eve of the ex-date on the market's calendar; through a ticker change before the pay date) (else at any time in the 45 days to the pay date: `src/taxjson/lib/positions_check.py` — `income_share_mismatches`, `INCOME_WINDOW_DAYS`); `tjs fetch --positions` writes one for Questrade. `taxjson-export` reads one back (`--holdings-toml`, `--report`) with the same cost rules, keeping `as_of`, `asset_type = "crypto"`, the `base_*` figures, `cost_source`, `cost_kind` and a `cost_currency` apart from `currency`, and refusing a `holdings_count` that is not its number of tables, and (`--holdings-toml`) a lot table with `acquired`, which one table per symbol cannot keep. Never booked by `tjs run`. Both countries. Reader: `src/taxjson/lib/positions_reports.py` — `_read_toml`; `src/taxjson/bin/taxjson_export.py` — `_holdings_toml_to_inventory`.

| Key | Meaning |
| --- | --- |
| `[meta] as_of` | the positions' date (else the date of `generated_at`); `taxjson-export` writes the date of the snapshots it reads back, when they are all of one day |
| `[meta] account` / `broker_account` | the broker account (masked in output; two ids are two accounts even when they mask alike); `taxjson-export` carries them when every input is a snapshot naming the same one |
| `[meta] holdings_count` | optional: the number of `[[holding]]` tables the writer wrote (`tjs fetch --positions`, `taxjson-export`); a file whose tables are not that many is refused as cut short or edited |
| `[[holding]] symbol`, `quantity`, `currency` | required per position; a future's `/` or `\` prefix is read `F:`, as the books spell it (an option's `underlying` too) |
| `total_cost` (or `book_cost`, `cost_basis`) | the position's cost; else `average_entry_price`, a price per share, × quantity, × `multiplier` (or `contract_multiplier`) for an option, 100 when none is declared (Questrade's snapshot states an option's price per share); else `cost_per_share` × quantity (per holding unit, so per contract for an option, as `taxjson-export` writes it) |
| `cost_kind` | `average` (a Canadian book cost) or `lots` (a per-lot basis) |
| `acquired` | `YYYY-MM-DD`: this table is one lot bought that day (several tables of one symbol are several lots); a US opening needs it |
| `multiplier` (or `contract_multiplier`) | optional: an option's contract size, a positive number (0, a negative or a non-number is refused) |
| `contract_multiplier_unknown` | optional: `true` when the contract size is not known (`taxjson-export` writes it when its inputs disagree): no cost is derived from a per-share price; refused beside a contract size, or when not `true` / `false` |
| `cost_unknown` | optional: `true` when the writer has no cost for the position (`taxjson-export` writes it, with no cost figure, for units moved in from another of your accounts or an opening at an unknown cost): no cost is compared |
| `cost_source` | optional: `"blended"` when `taxjson-export` took the position's cost from the passes across accounts (`tjs list`'s figure: units moved in from another of your accounts, whose cost the account's own books do not have); read as any other cost |
| `cost_currency`, `account`, `asset_type` (`cash` rows are skipped, `crypto`), `market_value` | optional; market value is never a cost; `cost_currency` is the currency of the cost when it is not `currency` (a CAD book value of a USD listing) |

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

What `tjs slip-audit` compares with the books' income (Canada only; tax-logic `CA-SLIP-01` to `CA-SLIP-03`). Never booked by `tjs run`: a slip changes no figure. Reader: `src/taxjson/lib/slip_audit.py` — `load_slips_file`, `ib_slips`, `find_ib_reports`; `src/taxjson/lib/ib_dividends.py` — `read_report`, `is_dividends_report`.

`slips.toml` holds the slips you type from the PDFs, one `[[slip]]` table per slip (`tjs slip-audit --template` prints one with a T5 per account and currency that has income). Type each box as the slip prints it (a thousands comma is fine; IB's T3 prints `405 54` for 405.54: type `405.54`), and leave out the boxes it leaves blank. Never type a name, a SIN or an address.

| Key | Meaning |
| --- | --- |
| `year` | optional; must be the project's year (last year's file is refused) |
| `[[slip]] type` | `T5`, `T3` or `T5008` (an NR4 is refused: compare it by hand) |
| `account` | required: the taxjson account (`[accounts.NAME]`, taxable) the slip's payments are in |
| `issuer` | a label for the payer or broker (`RBC`, `IB`) |
| `currency` | the slip's currency (T5 box 27; RBC and Webull send one T5 per currency); default `CAD` |
| `broker_account` | optional: the account number on the slip, only to tell two broker accounts of one account label apart; it is hashed the way the books keep it and never printed (shown as its first two characters and `***`) |
| `broker_key` | what `--import-cra` writes instead: a 10-character key of the broker account the slip's payments match (a CRA copy shows no account number), salted with the project's own salt in `work/.slip_key_salt` so it cannot be turned back into the account number; the slip is shown with that account's input files. A key written with another salt (work/ deleted, another project) matches no account and is said: import the slip again. `tjs redact` replaces it in its copy |
| `status` | `original` or `amended`, as `--import-cra` reads it from the slip line (a typed slip may leave it out); an amended slip replaces the original of the same issuer, account, fund, broker account and currency: `slip-audit` compares the amended slip alone (one of the same type and currency; two amended slips of one slip, or two originals it may amend, are all compared and listed under `not_compared`). A T3's fund is its `security`, or the funds its `[[slip.line]]`s name: an amended T3 naming no fund replaces no slip, and the audit says so |
| `source` | where the table came from: `cra:<file>` for an imported CRA slip (`cra:<file> #2` for the second slip of one PDF); IB's dividends report for the same broker account then keeps only its payments |
| `security` | a T3's fund (`XYZQ.TO`, or its root): its boxes split that fund's distribution |
| `code` | a T5008's type code (`SHS`, `OPC` ...): an aggregated T5008 is compared per class, as information |
| `boxes` | `{ BOX = AMOUNT }`. Compared: T5 24, 10, 18, 15, 16, 13, 14; T3 49, 23, 21, 24, 25, 26, 33, 34, 42; T5008 20, 21. Accepted and not compared: the derived boxes (T5 25, 26, 11, 12; T3 50, 51, 32, 39, 30 ...). Refused: the identifier boxes (recipient, account, report code) |
| `[[slip.line]] symbol`, `date`, `box`, `amount` | optional per-security lines (an RBC summary page, a split-corp page): a box-18 or box-42 line makes the suggestions name the security; `date` is optional: a dated box-42 line's suggested ADJUST goes on that distribution (with its record date), only the undated rest is spread over the year. A slip's lines are the detail of its boxes: a fund's line repeating its `security` box counts once |
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

**The slips CRA holds** (My Account › Tax information slips, one PDF per slip, printed from the browser): `tjs slip-audit [ACCOUNT] --import-cra <folder or PDFs>` reads them with `pdftotext` (poppler-utils) — the slip line (year, T5 or T3, issuer) and the box rows only; the page's name, address and SIN are never read — and shows the `[[slip]]` tables it would add, each placed by the books: a T5 in the broker account of the issuer whose dividends, withholding and interest it matches (one broker's slips shared out among its accounts), a T3 in the fund (`security`) whose distributions match it. Each slip counts once: a PDF holding two slips is read as two; a slip read twice (a second download, a file named with its folder) or already in `slips.toml` with the same boxes is not added again; an amended slip replaces its original — in the same import, or a table already in `slips.toml`, which is commented out under "# Replaced by the amended slip …" (the file before is kept as `slips.toml.bak`) — and an original whose amended slip is there is not added. `--write` writes them to `slips.toml` (checked to read back first); `--json` prints the import as JSON. A T5's broker is the one whose whole name its issuer carries, or whose carrying dealer the issuer is: some brokers' slips are issued by the dealer that holds their accounts, named in the shipped `src/taxjson/data/slip_issuers.toml` (`[[alias]]`: `broker`, `issuer` — each language half's name, matched word for word in one half of CRA's "ENGLISH INC./FRENCH INC" with case, punctuation and the legal form ignored — and `source`). It lists "CI INVESTMENT SERVICES" for Webull Canada; another CI business (CI Direct Investing, CI Global Asset Management) is not matched. Such a slip is placed only in a broker account of that broker the books hold; a broker account whose export has no income rows (Webull's export books trades only) is one when none of that broker's accounts has payments in the books, and two such cannot be told apart. A slip it cannot place (a bank's T5, a broker whose whole name the issuer does not carry — "TD DIRECT INVESTING" is not RBC Direct Investing — a carrying dealer's slip in a project without that broker, or a USD slip with no rate in the FX cache) is listed: import it again with ACCOUNT to put it in that account. Other PDFs (an RRSP receipt, a T2202), a French page and a cancelled slip are skipped, said. Code: `src/taxjson/lib/cra_slips.py` — `parse_slips`, `read_pdf`, `broker_of_issuer`, `issuer_aliases`, `place`, `plan_import`, `table`.

IB's dividends report (Reports › Tax › Dividend report, saved as CSV: `U1234567.2025.dividends.csv`) is read as is, payment by payment: each payment's `RevenueComponent` rows give its slip boxes (`T5: Eligible Dividend Income` box 24, `T5: Capital Gains` box 18, `T3: Eligible Dividend Income` box 49, `T3: Foreign Non-Business Income` box 25, `T3: Return of Capital` box 42, `T3: Capital Gains` box 21, a plain `Ordinary Dividend` box 24 for a Canadian issuer and box 15 for a foreign one; `Withhold` box 16 / 34), at IB's rate (`GrossInBase`) — for an account whose base currency is not CAD, each payment at the Bank of Canada rate of its pay date instead (a Canadian slip is in CAD; tax-logic `CA-SLIP-02`). Its `Account` section names the holder: taxjson reads only the account number (hashed, masked when shown) and the base currency; a report of two accounts is refused (its payments do not say which account they are in), and two copies of one report are read once (two downloads that differ in any payment, its base-currency amounts included, are refused: keep the newer). The report is matched to the project account whose books carry that IB account; `reconcile-slips` and the checklist's T5008 step skip it. The report has no interest: type the T5's box 13 as a `[[slip]]` with the IB account as `broker_account` to compare it.

---

## inputs/ACCOUNT/manifest.json: corporate-action elections

Your tax election for each merger, spin-off or name change, keyed by event id; written by `tjs run`'s prompt or `tjs elect ACCOUNT --set EVENT_ID=ELECTION --hint KEY=VALUE`. Commit it. Reader: `src/taxjson/lib/corp_actions.py` — `Manifest`, `HINTS_BY_ELECTION`, `RULES_BY_COUNTRY`.

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

Every hint is a non-negative amount. A run with `--no-input` leaves unresolved events in `work/pending_elections.json`; `tjs elect --pending` shows them with ready-to-copy `--set` lines. An election for an event whose id hashes a symbol an export spelled with a lower-case letter (RBC) also keeps `"read_event_id"`, the id that spelling gave: written by taxjson, never typed. A ticker.map `DISTINCT` line written later, keeping that spelling apart from its upper-case one, sends the election to the event read in that spelling, and the upper-case spelling's event is asked about (`src/taxjson/lib/corp_actions.py` — `_respelled`).

---

## inputs/CRYPTO ACCOUNT/sends.json and crypto_sends.tt

`sends.json` holds your decision for each crypto send that did not arrive in another of your crypto accounts; `tjs crypto-sends --set ID=DECISION` writes it. Commit it. `crypto_sends.tt` is generated from it (with a shared `inputs_dir`, in the year's `work/crypto_sends/<account>/`: a run never writes the shared folder) (one sale at fair value per gift or payment, and per network fee): never edit it; a hand-written file of that name is never overwritten. Reader: `src/taxjson/lib/crypto_sends.py` — `load_decisions`, `record_decision`, `DECISIONS`, `REFUSED`.

```json
{"schema_version": 1, "sends": {
  "<send id>": {"decision": "gift", "note": "birthday", "price": 61.25,
                "price_currency": "CAD"},
  "<send id>-fee": {"decision": "fee", "price": 61.25,
                    "price_currency": "CAD"}},
 "arrivals": {
  "in-<arrival id>": {"decision": "new", "note": "from outside"}}}
```

| Key | Meaning |
| --- | --- |
| `decision` | `self` (your own wallet), `gift`, `payment`; a `-fee` id takes `fee` |
| `price` | fair value per coin when the lookup fails (required for `fee`), in `price_currency` |
| `price_currency` | the base currency `price` was entered in (written by `--set … --price`); another base converts it at the day's rate, and with no rate the send is unpriced. Missing (an older decision): read in the current base currency |
| `note` | kept with the decision and written into the `.tt` |
| `unpair` | `true`: keep a send the tool paired out of the pairing (that arrival was unrelated) |
| `summary` | written by the tool |

`arrivals` is optional: what a stablecoin arrival that pairs with no send is, for the US-dollar pool of a project not in US dollars (`tjs crypto-sends ACCOUNT --set in-ID=new|return`; the listing names the ids it takes back). `new`: new money, at the day's rate in the pool, whatever was sent earlier. `return`: the coins a `self` send parked coming back, no new cost. With no decision, an arrival of a coin a `self` send parked is taken back up to the units parked, and the rest is new money. Another coin is always new money. A `return` with nothing parked comes in as new money, and the listing says so. Only the pool (the currency gain `tjs crypto-sends` shows for a stablecoin send) reads it. Reader: `src/taxjson/lib/crypto_sends.py` — `record_arrival`, `ARRIVAL_DECISIONS`, `usd_pool`.

Country: `gift` is Canada only (a disposition at fair value, s.69(1)(b)); a US project refuses it. taxjson cannot book a US gift yet: `self` keeps the coins and their basis, so a later sale uses them (see `docs/limits.md`).

---

## filed/YEAR.json: close-year locks

Written by `tjs close-year`: the closed year's sales, year-end positions and cost, its country and date basis, the trades and income rows of December and January (`boundary_rows`; `tjs handoff` compares the sales and income rows with a tax date of Dec 30 or Dec 31, or income dated in January, to the cent, and reads the rest for the rows the two years date on different sides of Dec 31: each row's `net` and `income` in the base currency — `income` is what the income report counts, a dividend's gross, a trade's net — and, as the broker stated them, `native_currency`, `native_income` and `native_net`, or, for a dividend whose same-day TAX rows the run netted into its net, `native_withheld` instead of `native_net`: `{ CURRENCY: AMOUNT }`, the tax withheld in the currency each TAX row states it in, or, where two or more dividends of the security share the day's TAX rows, `native_withheld_day` on each of them: the day's, compared once; none of these for a row whose amounts are taxjson's, a crypto row priced from the price cache; a lock written before the release after v1.1.0 has `net` only, and handoff compares its rows at that net within 0.5%, with a note), and the carry-forwards (net capital loss or US short/long-term carryover; Canada's minimum tax carryover), the net capital loss with `carry_back_incomplete` when its split was locked on a ledger that left sales with an unknown cost out, `carry_back_stated` when it rests on a `[carryover] carried_back` answer (the next years' projects read it), `carry_back_stated_to` for those answers given by the year the carry-back went to, and, beside `carried_back`, `carried_back_to` (the years it goes to, `{ YEAR: AMOUNT }`; the next years' ledgers read it) with `carried_back_split: "oldest_first"` when those years are `tjs carryover`'s proposal and another split was open (a lock written before the release after v1.1.0 has none of the three, and is read as a total met the oldest year first); with `fx_cash_ledger = "v2"` and a computed ledger, `fx_cash_v2`: the next year's opening pool, as `year`, `currency` (the base currency its costs are in), `books` (each cash book's foreign cash and its cost at Dec 31; a debt as negative units) and `accounts` (each book's broker account, hashed as the books are, so a book renamed when the broker's accounts change still opens under its new name), and `transit` when a move between your own accounts left before Dec 31 and arrives after it (its units and cost by currency, matched by the arrival next year), and `early_out` when one arrived before Dec 31 and leaves its account after it (its units by book and currency, matched by the departure next year). The next year's v2 ledger refuses to carry a close of another base currency, or one in a lock closed under the other country's rules: it is not computed, with a `carry` problem naming the record, until the setting is fixed or `CASHOPEN` lines open the year. Read by `tjs check-filed`, `tjs years`, `tjs handoff`, `tjs carryover`, `tjs option-boundary` and the next year's estimate and `amt` (through `prior_year_record`). Commit it; do not edit it. These readers apply one check, so they agree: a lock that cannot be read or is damaged (a figure that is not a number, a carry-forward section without its closing balance, a year of origin written twice, a negative balance, a minimum tax `closing` that is not the sum of its `closing_by_year`, a `year` other than its file's, a key written twice) is refused by name (`check-filed` and `years` call it damaged), and a lock closed under the other country's rules is refused by check-filed, handoff, carryover, the estimate and `amt`.

---

## `tjs years --json`

The year projects of a folder of exports shared by every year (`tjs years` there or in a year folder): a stable schema for programs (new keys may be added; none is renamed or removed while `schema_version` is 2). Code: `src/taxjson/lib/years_report.py` — `years_report`.

| Key | Meaning |
| --- | --- |
| `schema_version`, `root`, `newest` | `2` (`1` up to v0.35.0: see upgrading.md), the folder holding the year folders, the newest year |
| `years` | one object per year folder (`YYYY/` with a `taxjson.toml`), oldest first (below) |

Each year: `year`, `folder`; `filed` (its `filed/<year>.json` exists; also `true` for a year whose lock was deleted while its taxjson.toml still carries close-year's freeze note or its `filed/<year>/reports/` still holds the reports close-year kept, with `problem` saying to restore it), `closed_at`, `partial_lock` (taken before the year ended), `totals` (`realized`, `disallowed`, `income` of the lock; `realized` and `disallowed` as filed, the RETURN row of `tjs sum` the lock recorded as `return_row`, when it has one: the drift check compares the lock's engine totals, a cent or two apart on a large year; `income` as `tjs sum`'s taxable SUBTOTAL adds it, each account's DIVIDEND and PIL to the cent, a cent or so from the lock's total); `last_run` (the last full run, ISO), `stale` (`true` when the inputs or settings changed since; `null` when there is no run), `changed` (what changed); `differs_from_newest` (`{"map_rules", "tobase_rules", "keys"}`: how many ticker.map rules, tobase.map lines — per-year copies only; a shared file is compared as its `tobase_map` setting, one of the keys — and settings differ from the newest year's, the accounts' order counting as one; `null` for the newest, and when a taxjson.toml or ticker.map cannot be read: `problem` names it); `tobase_map` (the tobase.map the year reads, from the folder holding the years: `tobase.map` when shared, `2025/tobase.map` for a copy of its own, `null` for none), `tobase_shared` (`true` when it is the shared one) and `tobase_frozen` (`true` for a filed year's own copy that `close-year` froze: the year's by design, never a copy for `tjs migrate` to merge); `drifted` (the last drift check of a filed year — `tjs run` or `check-filed` there — on the lock as it is now: `true` when it DRIFTED, `false` when it matched, `null` when there is none, the lock was closed again since, or it is damaged); `shared_exports_changed` (a year closed with its inputs frozen: the shared exports, from the folder holding the years, that gained, changed or lost a row of the year since the close (a new file holding a row of the year too; each read as the parsers read it, a UTF-16 export included), the generic mappings (`<csv>.toml`, `generic.toml`) that now read one of the year's CSVs holding a row of the year otherwise than the copy (compared as parsed TOML: a comment is no change), and the `manifest.json` / `sends.json` holding an election or send decision on the year that differs from the year's copy — the year reads its frozen copy, so a correction saved there reaches neither it nor the next year's handoff; `[]` for an open year, a damaged lock, a lock that froze nothing; the table's INPUTS says `shared changed`); `shared_not_compared` (`[{"file", "why"}]`: a file of that comparison that could not be read — not UTF-8 or UTF-16 text, not TOML, no permission — so not judged; the table's INPUTS says `shared unchecked` and check-filed names it); `marks` (`{"done", "forced"}`: the year's checklist marks, plain and `--force`d); `problem` (a file that cannot be read, a `filed/<year>.json` that is not a close-year lock or was deleted from a closed year, or a year closed with its inputs frozen whose `[settings]` names the shared `inputs_dir` or `tobase_map` again, else `null`; `filed` stays `true` and the table says `lock damaged`, or `lock missing` for a deleted lock).

## `tjs journals --json`

A stable schema for programs (a checklist, a script): new keys may be added, none is renamed or removed while `schema_version` is 1 and `format` stays `taxjson-journals/1`. Read from the last run's `work/cross_listings.state`, the parsed exports in `work/` and the map the books were merged with; `tjs journals` refuses a project with no completed run. Code: `src/taxjson/lib/journals.py` — `report`, `FORMAT`, `SOURCES`.

| Key | Meaning |
| --- | --- |
| `format` | `"taxjson-journals/1"` |
| `schema_version` | `1`, beside `format` (both stay; see `--json` outputs below) |
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

## `tjs ticker-map --suggest --json`

The ticker.map lines the last run suggested, as one document (new keys may be added). Under `--write` the JSON is still the only thing on stdout: the plain lines (`Not added …`, `Nothing added to ticker.map.`, `Added N line(s) …`, the questions on a terminal) go to stderr, and `--write` with no terminal and no `--all` stops (exit 2) before any output while there is anything to choose — an evidence line or a pair to verify. Code: `src/taxjson/cli/maps.py` — `cmd_ticker_map`; `src/taxjson/lib/ticker_map_suggest.py` — `record`.

| Key | Meaning |
| --- | --- |
| `schema_version` | `1` |
| `suggestions` | the lines the run's evidence names (`certainty` `"evidence"`): what `--write --all` appends; a script may append each one |
| `verify` | the listing pairs only you can answer (`kind` `"map-gap"`, `certainty` `"verify"`): `line` is the `TOBASE` (one security), `alternative` the `DISTINCT` (two); never written without your answer — keep them out of a script that appends `suggestions` |
| `skipped` | the lines left out, each with `why` and `by` (`"ticker.map"`: the map already answers it; `"suggestion"`: another suggestion covers it) |
| `unused` | the rename rules no symbol of the books reaches (`kind` `"unused-rule"`, `rule`, optional `hint`): to delete by hand, never written |
| `unused_unread`, `map_gap_unread` | the parsed sources the unused-rule and listing-pair checks could not read (their lists are then incomplete) |

Each suggestion: `line`, `reason`, `source`, `kind`, `certainty`, and when they apply `alternative`, `template` (`true`: a placeholder to edit, never written) and `tt` (`true`: a `.tt` line for an account's file, never written to ticker.map).

---

## `tjs checklist --json`

The checklist as one document: a stable schema for programs (new keys,
statuses and items may be added; none is renamed or removed while
`schema_version` is 3). Inside a project `--run` runs the commands that
prove the checks and keeps the verdicts; without `--run` the kept
verdicts are reported (`checked`, `stale`); outside a project nothing
is evaluated.
Code: `src/taxjson/lib/checklist.py` — `to_json`, `guide_json`,
`SCHEMA_VERSION`, `STATUSES`, `RunInfo`.

| Key | Meaning |
| --- | --- |
| `schema_version` | `3` (`2` up to v0.35.0: see upgrading.md) |
| `in_project` | `true` when the folder (or `-C DIR`) holds a taxjson.toml |
| `year`, `country` | the project's `[settings]`; `null` outside a project |
| `all_passed` | `true` when no step is open (the exit is then 0); `null` outside a project |
| `counts` | `{"done", "forced", "attention", "todo", "blocked", "manual", "review", "n/a"}`: how many steps have each effective status; `null` outside a project |
| `checked`, `checked_oldest` | when the newest and the oldest verdict shown were made (ISO, local time; they differ after a `--run --quick` or `--run --only ID` kept older verdicts); `null` when no step was checked yet, and outside a project |
| `stale` | what moved since the verdicts were made, one sentence each (the inputs changed, the books were rebuilt by `tjs run`, taxjson was upgraded); `[]` right after a `--run` |
| `next` | `{"step", "id", "title", "do"}` of the next step: the first one to do, needing attention or blocked that can be acted on now, else the first one to confirm (`do`: what to do, usually its first command); `null` when none, and outside a project |
| `steps` | one object per step, in order (below); with `--only ID` that step only, with `--section N[,N]` those sections' steps only |

Each step:

| Key | Meaning |
| --- | --- |
| `step`, `id` | its number (1, 2 ...) and a stable id (`install`, `inputs-frozen`, `run-clean`, `missing-history`, `t5008`, `filed-lock` ...); `--done` / `--undo` / `--only` take either, several comma-separated (`--done export-coverage,inputs-frozen`); `checklist.json` records the id |
| `section`, `title` | the section it belongs to (`Set up`, `Get your files`, `Build the books`, `Fill the gaps`, `Tidy the config`, `Check`, `Results`, `Through the year`, `Share safely`, `Year end`) and what it is (a check's title in the project's country's words) |
| `check` | `true` for a check (a detector proves it), `false` for a step marked from the project's files |
| `command` | a check's proving command as one line (e.g. `tjs find-missing-history`); a step's first command |
| `commands` | `[{"command", "note", "country"}]`: the lines to type, a short note, and `"canada"` / `"usa"` for a command only one country has (`null` for both); inside a project the other country's are left out |
| `how`, `why` | what to do besides the commands (may be empty), and why the step matters |
| `status` | what the detector or the step's rule found: `done`, `attention`, `todo`, `manual` (only you can confirm it), `blocked` (a prerequisite, usually `tjs run`, is missing), `review` (yours to run and read: taxjson cannot tell whether you did) or `n/a`; `null` outside a project |
| `effective` | the status with your mark applied: `forced` for a `--done --force` mark (a `skipped` mark of releases before v0.35 reads as one), `done` for a done mark (never over a finding: a done mark on an `attention` or `blocked` step leaves it so, unless the attention was a question the mark answered; `--done` is refused while the kept verdict is one) |
| `detail` | what the check or the project's files say about it (empty when nothing to say) |
| `override`, `note`, `finding` | your mark (`done`, `forced` or `null`), its note, and the detector's finding when a mark sits beside one |
| `next` | `true` for the step `next` names |

---

## `tjs slip-audit --json`

The slip audit as one document: a stable schema for programs (new keys may be added; none is renamed or removed while `schema_version` is 1). Code: `src/taxjson/lib/slip_audit.py` — `audit`, `SCHEMA_VERSION`, `CATEGORIES`, `question_keys`.

| Key | Meaning |
| --- | --- |
| `schema_version`, `year`, `country` | `1`, the tax year, `"canada"` |
| `tolerance`, `fx_band` | the allowed difference per box (CAD; each line's `tolerance` is in its bucket's currency), and the share of a box's converted money also allowed (`0.005`) |
| `sources` | the slip sources read: `{"kind": "slips.toml", "file", "slips"}` or `{"kind": "ib-dividends", "file", "account", "broker_account" (masked), "payments", "unknown_components", "problems"}`, with `"converted_from"` (the report's base currency) when its payments were converted to CAD at the Bank of Canada's daily rates |
| `annual_average` | `{CUR: {"rate", "observations", "source"} \| null}` for each foreign currency in sight |
| `accounts` | `[{"account", "status", "groups"}]`; a group is the slips of one broker account (or of the whole account): `{"broker_account", "slips", "buckets", "payments", "securities", "record_year"}`; each of `securities` is a fund the slips give box 18 or 42 for: `{"symbol", "currency", "slip", "books"}`, with `"roc_status"` when its return of capital differs |
| `buckets[]` | per slip currency: `{"currency", "slips", "split_by_t3", "lines"}`; each line `{"category", "label", "boxes", "slip", "books", "diff", "tolerance", "status" (ok, differs, under-50), "converted", "books_annual_average", "closest" (daily, annual or null)}`, and in a foreign currency `"books_cad_daily"`, `"slip_cad_annual_average"` |
| `payments` | IB's payments against the books: `matched`, `missing_from_books`, `missing_from_slip`, `amount_differs`, `currency_differs` (a payment whose only book row within 7 days is in another currency: `currency` and `slip` are the report's, `books`, `books_currency`, `books_date`, `books_symbol` the books'), `other_year` |
| `coverage` | `no_slip` (an account's income with no slip; `small_interest_only` when it is interest under 50) and `slips_without_books` |
| `t5008` | aggregated T5008 slips against the books' dispositions, per class (information) |
| `suggestions` | `capital_gains_dividends` (`{"symbol", "date", "year", "amount", "account", "toml"}`), `tt_lines` (`{"account", "file", "lines", "shared"}`: `file` from the project folder, in the folder the project reads its exports from, `../inputs/<account>/slip-audit.tt` with `inputs_dir`; `shared` true then, every year reading that folder reading the lines), `notes` |
| `problems` | every input note, as text: what was not compared (below) and the notices that change nothing (a second copy of a report, read once; a pay-dated ROC line counted in its record year; a `broker_key` written unsalted) |
| `not_compared` | what the audit could not compare, `{"kind", "account", "text"}`: `no-books` (an account with inputs and no readable `work/<account>_base.json`), `report-no-account` (an IB report no account's books carry; `account` null), `unknown-component` (an IB component taxjson does not know, with an amount), `broker-key` (a `broker_key` no broker account matches), `amended-slip` (an amended slip whose original cannot be told), `report-mismatch` (an IB payment whose components do not add up to its Summary row: its slip boxes are in doubt) |
| `issues`, `status` | the findings `{"account", "kind", "text"}` (kinds: `differs`, `no-slip`, `no-books`, `empty-slip`, `missing-from-books`, `missing-from-slip`, `amount-differs`, `currency-differs`, `security-differs` (a fund's return of capital differs from its slip's while the account's total agrees: another fund's offsets it; its `securities[]` record says `"roc_status": "differs"`)) and the status: `attention` (slips and a finding), else `incomplete` (something in `not_compared`), else `no-slips` or `ok`; the command exits 1 when `issues` or `not_compared` is not empty |

`tjs slip-audit --import-cra … --json` prints the import instead (`schema_version` 1): `year`, `slips` (each slip read: `file`, `type`, `status`, `issuer`, `currency`, `boxes`, `account`, `broker_account` (its input files), `fund`, `how`), `add` (the files whose tables are added), `replace` (`{"table", "by"}`: a table in slips.toml an amended slip replaces), `replaced_in_import`, `not_imported`, `tables` (the TOML text), `written`, `backup`. Code: `src/taxjson/cli/slips.py` — `_slip_audit_import_cra`.

---

## `tjs technicals --json`

Each holding's statistics from its daily closes (docs/commands.md, `tjs technicals`): market statistics, no tax figure. New keys may be added; none is renamed or removed while `schema_version` is 1. Code: `src/taxjson/cli/technicals.py` — `_json_doc`, `_stats_rows`; `src/taxjson/lib/technicals.py` — `series_stats`, `blank_reasons`, `MIN_SESSIONS`; `src/taxjson/lib/price_chain.py` — `daily_history`, `HISTORY_TODAY`.

| Key | Meaning |
| --- | --- |
| `as_of` | the books' last day (their horizon), or `null` |
| `adjusted` | `"splits+dividends"`: every series is Yahoo's adjusted one |
| `latest_close_date` | the newest last close of any row; a row with an older one has `stale: true` |
| `benchmark` | `null` without one, else `symbol`, `given_by` (`"--benchmark"` or `"[settings] benchmark"`), `history`, `cache_reason`, `quote_symbol`, `currency`, `sessions`, `last_date`, `fetched` (as a row's) |
| `windows`, `min_sessions` | each statistic's window, and the closes each needs (`min_sessions`, by the `stats` keys) |
| `left_out` | `{"options", "futures", "crypto"}`: the symbols left out, by kind |
| `rows` | one object per symbol held (below), by symbol |

Each row: `symbol`, `qty` and `accounts` (the quantity summed, and by account), `quote_symbol` (the Yahoo spelling asked: a ticker.map `QUOTE` line's, else taxjson's; `null` when there is none) and `quote_ratio`, `currency` (the series', never converted), `source` (`"yfinance"` or `"cache"`), `fetched` (the day the series was fetched), `sessions`, `first_date`, `last_date`, `last_close` (adjusted, in `currency`), `stale`, `paired_returns` (the daily returns on dates the benchmark has too; `null` without one), `stats` (every statistic: `sma20_pct`, `sma50_pct`, `sma200_pct`, `rsi14`, `beta`, `alpha_pct`, `from_high_pct`, `from_low_pct`, `volatility_pct`, `return_1m_pct`, `return_3m_pct`, `return_1y_pct`, `max_drawdown_pct`, `atr14_pct` at 4 places, `avg_volume_20` at 2; `null` when blank, never `-0.0`), and:

| Key | Values |
| --- | --- |
| `history` | `"ok"`: a series was read. `"offline"`: none cached and `TAXJSON_OFFLINE` set. `"no-extra"`: none cached and no `[fx]` extra. `"none"`: Yahoo was asked and gave nothing (or the tier failed), none cached. `"no-spelling"`: taxjson has no Yahoo spelling for the symbol and no `QUOTE` line gives one. The benchmark's has the same values. |
| `cache_reason` | why a cached series was read: `null` when Yahoo answered on this run; `"today"`: the entry was fetched today (not asked again); `"none"`: Yahoo gave nothing; `"offline"`, `"no-extra"`: as `history`'s. `null` for a row with no series too. |
| `blank` | `{stat: why}` for each `null` in `stats`: `"history"` (fewer closes than its window), `"paired"` (fewer than 252 returns on dates the benchmark has too), `"no-benchmark"`, `"flat"` (RSI: no close moved), `"flat-benchmark"` (the benchmark's returns do not vary), `"no-high-low"` (ATR: fewer than 15 sessions in a row with a high and a low), `"no-volume"` (a session of the last 20 has no volume); `{}` for a row with no series |

## `--json` outputs

Every `--json` output is one JSON object whose top-level `schema_version` is the integer below: a command's document, or a mode's that prints one of its own (`YEAR` is a 4-digit year, `a/b` either flag), or a taxjson-* tool's that no command prints (the tools a command runs print the command's). A variant of an output (an empty one, not applicable, nothing to compare, the other country's form) carries its number. New keys may be added at any depth, and new values of a status or a kind; while the number stays, none is renamed, moved or removed, and no value changes its type, unit or meaning: a change that does raises it, in docs/upgrading.md. Key order and indentation are not part of it. A refused command prints its `Error:` on stderr and nothing on stdout; a finding (exit 1) still prints its document. Code: `src/taxjson/lib/json_contract.py` — `SCHEMAS`, `TOOL_OUTPUTS`, `versioned`; `tests/test_json_contract.py` holds each output to a golden view or a test, and to its row here.

| Output | `schema_version` |
| --- | --- |
| `checklist` | 3 |
| `checklist --done/--undo/--reset` | 1 |
| `years` | 2 |
| `years --diff` | 1 |
| `align` | 1 |
| `align --write` | 1 |
| `fetch` | 1 |
| `fetch --list` | 1 |
| `elect` | 1 |
| `elect --pending` | 1 |
| `ticker-map --suggest` | 1 |
| `update-tobase-map` | 1 |
| `compare` | 1 |
| `compare --save` | 1 |
| `compare --delete` | 1 |
| `compare --list` | 1 |
| `snapshot` | 1 |
| `clean` | 1 |
| `crypto-sends` | 1 |
| `amt` | 1 |
| `amt YEAR` | 1 |
| `div-estimate` | 1 |
| `estimate` | 1 |
| `fx-cash` | 1 |
| `fx-cash --ledger v2` | 1 |
| `instalments` | 1 |
| `option-yield` | 1 |
| `plot-tax` | 1 |
| `stats` | 1 |
| `sum` | 1 |
| `win-rates` | 1 |
| `list` | 1 |
| `shares` | 1 |
| `technicals` | 1 |
| `cash` | 1 |
| `dil` | 1 |
| `divs` | 1 |
| `events` | 1 |
| `fees` | 1 |
| `gains` | 1 |
| `leaps` | 1 |
| `roc` | 1 |
| `staking` | 1 |
| `trades` | 1 |
| `transfers` | 1 |
| `ccd-sum` | 1 |
| `dil-sum` | 1 |
| `divs-sum` | 1 |
| `fees-sum` | 1 |
| `leaps-sum` | 1 |
| `losers` | 1 |
| `roc-sum` | 1 |
| `staking-sum` | 1 |
| `trades-sum` | 1 |
| `winners` | 1 |
| `wash-radar` | 1 |
| `buy-check` | 1 |
| `sell-check` | 1 |
| `rebuy` | 1 |
| `harvest` | 1 |
| `tips` | 2 |
| `watch` | 1 |
| `form-export` | 1 |
| `t1135` | 2 |
| `reconcile-slips` | 1 |
| `slip-audit` | 1 |
| `slip-audit --import-cra` | 1 |
| `carryover` | 2 |
| `option-boundary` | 1 |
| `handoff` | 1 |
| `audit` | 1 |
| `wash-sales` | 1 |
| `tax-logic` | 1 |
| `edge-cases` | 1 |
| `check-dates` | 1 |
| `check-tt` | 1 |
| `sanity` | 1 |
| `journals` | 1 |
| `renames` | 1 |
| `spinoffs` | 1 |
| `splits` | 1 |
| `channels` | 1 |
| `taxjson-sum-gains` | 1 |
| `taxjson-sum-income` | 1 |
| `taxjson-audit` | 1 |

---

## Old files and environment variables

- Old per-purpose files are no longer read and stop every command until `tjs migrate` folds them in: `yf_ticker.map` (QUOTE lines), `crypto_ticker.map` (CRYPTO), `ticker_extraction_overrides.txt` (EXTRACT), `t1135.map` (T1135), `amt_carryover.txt` (`[estimate] amt_carryover`), `claimed_losses.txt` (`[carryover] claimed`), `capital_gains_dividends.map` (`[[capital_gains_dividends]]`), `distributions.map` (`[[distributions]]`). `tv_exchange.map` is only renamed. Code: `src/taxjson/lib/migrate.py` — `legacy_files`.
- `TAXJSON_LOCAL_TZ`: the crypto time zone outside a project (the setting wins).
- `TAXJSON_LAYOUTS`: the folder of the user's generic-importer layouts, else `~/.config/taxjson/layouts`; set empty, no user folder (the tests' setting).
- `TAXJSON_TICKER_MAP`: set by `taxjson` for every stage; the ticker.map a stand-alone tool reads.
- `TAXJSON_PROJECT_ROOT`: set by `taxjson` for every stage; the project an input file in its (possibly shared, `inputs_dir`) inputs folder belongs to — the IB parse reads that project's `.tt` RENAME lines and ticker.map. Code: `src/taxjson/lib/project_layout.py` — `project_of_input`.
- `TAXJSON_WIDTH`: the column width text wraps at, for a person. Unset: the terminal's full width (at most 160, never under 40), or 120 when the output is piped or redirected; `0`: no wrapping (what the run writes to `work/` and `reports/` is always unwrapped). Code: `src/taxjson/lib/out.py` — `width`.
