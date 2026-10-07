# Settings and project files

Every key `taxjson.toml` accepts and every other file a project reads, with its meaning, default, country, when to change it and an example. All examples use made-up symbols and amounts.

A project is a folder:

| Path | What it is | Section |
| --- | --- | --- |
| `taxjson.toml` | the configuration | [taxjson.toml](#taxjsontoml) |
| `ticker.map` | symbol rules and lookups (optional) | [ticker.map](#tickermap) |
| `inputs/<account>/*.csv` | broker exports, detected by content | — |
| `inputs/<account>/*.tt` | hand-entered rows | [.tt files](#tt-files) |
| `inputs/<account>/generic_*.csv` + mapping | any other broker's CSV | [Generic importer mapping](#generic-importer-mapping) |
| `inputs/<account>/manifest.json` | your corporate-action elections | [manifest.json](#inputsaccountmanifestjson-corporate-action-elections) |
| `inputs/<crypto account>/sends.json`, `crypto_sends.tt` | your crypto-send decisions and the sales generated from them | [sends.json](#inputscrypto-accountsendsjson-and-crypto_sendstt) |
| `missing_history.json` | sales whose purchase is not in your files | [missing_history.json](#missing_historyjson) |
| holdings TOML (anywhere) | a broker's positions, for `taxjson sanity` / `taxjson opening` | [Holdings TOML](#holdings-toml) |
| `filed/<year>.json` | close-year locks | [filed/YEAR.json](#filedyearjson-close-year-locks) |
| `work/`, `reports/` | rebuilt by every `taxjson run`; never edited | — |

Rules every reader applies:

- `taxjson init` writes every key of the project's country, the optional ones commented out with their default; `taxjson format` puts an edited file back into that layout without changing what it configures.
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
- **Meaning:** the IANA zone crypto exchange rows (stamped in UTC) are dated in. Outside a project the `TAXJSON_LOCAL_TZ` environment variable is used.
- **Default:** none. A project with a `crypto = true` account stops until it is set; `taxjson init` writes this machine's zone when it can read one.
- **Country:** both.
- **Change it when:** you set up a crypto account, or you moved. A change re-dates every crypto row and re-keys the crypto sends.
- **Example:** `local_timezone = "America/Toronto"`

#### `prior_year_record`
- **Meaning:** the path to last year's close-year lock, read by `taxjson handoff`, `taxjson carryover`, `taxjson option-boundary`, `taxjson edge-cases` and the carry-forwards of the estimate.
- **Default:** none (then `filed/<year-1>.json` in this project, if any).
- **Country:** both.
- **Change it when:** each year's project lives in its own folder.
- **Example:** `prior_year_record = "../2024/filed/2024.json"`

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
- **Meaning:** `true` prints the FX-on-foreign-cash report at the end of `taxjson run` (Canada s.39(1.1) with its $200 exemption; US §988, ordinary income). `taxjson fx-cash` prints it on demand either way.
- **Default:** `false`.
- **Country:** both.
- **Change it when:** you hold material foreign cash and want the figure every run.
- **Example:** `fx_cash_gains = true`

#### Options

#### `option_premium_timing`
- **Meaning:** when a written option's premium is taxed: `grant` (a gain in the year written, ITA s.49(1); a buy-back is a loss in its own year) or `close` (netted at the closing transaction).
- **Default:** `grant`.
- **Country:** Canada only (a US premium is taxed at the close, §1234).
- **Change it when:** you filed earlier years on close timing and must stay consistent; see `taxjson option-boundary`.
- **Example:** `option_premium_timing = "grant"`

#### `option_grant_timing_since`
- **Meaning:** contracts written before this year keep close timing — the transition from books filed the old way.
- **Default:** the project's `year` (warned).
- **Country:** Canada only.
- **Change it when:** set it once, to the first year you file under grant timing, and keep it unchanged in every later year's project (do not bump it with `year`: `taxjson handoff` flags a written option carried across under another timing, taxed twice or in no return).
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
- **Change it when:** you want each contribution treated as a purchase and will declare custody moves (`.tt` TRANSFER ... DECLARED pairs).
- **Example:** `transfers_as_acquisitions = false`

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
- **Change it when:** a registered account has a neutral name; `taxjson scan` uses it (a US dividend payer in a TFSA is flagged).
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
- **Meaning:** positions files `taxjson sanity` reconciles against with no arguments (IB statements, RBC Holdings Exports, holdings TOMLs); a full `taxjson run` also checks them at the end and warns, never fails.
- **Default:** none.
- **Country:** both.
- **Change it when:** you keep the broker's year-end positions.
- **Example:** `holdings = ["~/broker/margin_holdings.toml"]`

#### `combined_broker_accounts`
- **Meaning:** `true` declares that every broker account in this folder's statements is yours and taxable together; the "statement spans N accounts" ATTENTION becomes a one-line note with masked ids.
- **Default:** `false`.
- **Country:** both.
- **Change it when:** one export covers two of your taxable accounts (a second IB account exported with the main one). Refused on a `sheltered` account unless the statement shows every account is the same plan.
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

One table per non-cash fund distribution (a reinvested capital-gains distribution, a late return-of-capital factor). `taxjson run` books each as a cost adjustment on the shares every taxable account holds on the record date. The income is on the slip; taxjson does not count it. Both countries.

#### `symbol`
- **Meaning:** the books' symbol (matched through ticker.map and renames).
- **Example:** `symbol = "XYZQ.TO"`

#### `record_date`
- **Meaning:** the record date, a TOML date (or a quoted `"YYYY-MM-DD"`).
- **Example:** `record_date = 2025-12-29`

#### `per_share`
- **Meaning:** the amount per share in the base currency (convert a US-listed fund's USD factor first); negative = return of capital; `0` is a placeholder and is not applied.
- **Example:** `per_share = 0.25`

### `[[capital_gains_dividends]]`

T5 box 18 capital-gains dividends the books carry as dividends: `taxjson divs-sum` shows them apart and the estimate taxes them as capital gains. One table per payment or per year. Canada only.

#### `symbol`
- **Meaning:** the books' symbol; a bare root covers only its Canadian listings.
- **Example:** `symbol = "ABCX.TO"`

#### `year`
- **Meaning:** every dividend of that tax year (instead of `date`).
- **Example:** `year = 2025`

#### `date`
- **Meaning:** one payment's date (instead of `year`).
- **Example:** `date = 2025-06-16`

#### `amount`
- **Meaning:** `"all"` or the box 18 amount in the dividend's currency.
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

The project's one mapping file, at the project root; one rule per line, symbols case-insensitive, notes after `#`. `taxjson run` refuses a map with a malformed line, a rename cycle, one symbol renamed to two targets, or a `DISTINCT` pair the renames pool together. `taxjson ticker-map --suggest --write` appends suggested lines. ticker.map holds standing truths only: a ticker change or a journal between two listings happens on a date and is a `.tt` line of an account, date first (`RENAME <date> OLD NEW`, `JOURNAL <date> FROM TO <qty>`; see [.tt files](#tt-files)). A map that still has the legacy `JOURNAL` or dated `RENAME` lines works (`JOURNAL` is read as `TOBASE`), with one Warning per run. `taxjson format-map` lays the file out the way `taxjson init` writes it: a short header, then the rules in groups, each under a `## --- <Group> ---` heading — Spellings (`GLOBAL`, an undated `RENAME`), Listings of one security (`TOBASE`, `DISTINCT`), Clean-up (`DELETE`), Lookups (`QUOTE`, `EXTRACT`, `CRYPTO`, `T1135` and the market lists), then Retired (`TRADINGVIEW`) and Unrecognized (lines `taxjson run` cannot use, kept as written) when there are any. It also migrates the dated events: each `JOURNAL A B` becomes `TOBASE A B`, and each dated `RENAME OLD NEW YYYY-MM-DD [late=…]` moves, with its comments, to `inputs/<account>/renames.tt` as `RENAME YYYY-MM-DD OLD NEW [late=…]` — in the first account whose books carry the change (else whose books hold the symbols, else the only account, else the first taxable one), one per account kind whose books hold the symbols: a `.tt` RENAME applies to every account of its kind. Before writing, it simulates the run: the rename events booked after the move (the project's `.tt` lines plus the moved ones) must be the ones booked now in every account, or nothing is written (exit 2, naming the lines that disagree); a moved line repeating a `.tt` line written differently is reported. Every target is checked first (a link to outside the project — the file or its `inputs/<account>` folder — is refused), the `.tt` files are written before the map, and a block a file already holds is not added again. The dry run shows the map diff and the `.tt` additions, and a `.tt` `JOURNAL` line for each migrated `JOURNAL` whose listings the holdings show long and short; `--check` fails while the map is not formatted, a migration is pending or `taxjson run` refuses the map. Your order is kept within a group, a comment directly above a line moves with it, and the parsed map (plus the moved lines) must mean the same (`src/taxjson/lib/ticker_map_format.py` — `format_map`, `GROUPS`, `Moved`; `src/taxjson/lib/dated_events.py` — `home_accounts`, `plan_migration`). The parsers are `src/taxjson/bin/taxjson_ticker_map.py` — `_parse_map_file`, `merge_renames`; `src/taxjson/lib/ticker_map.py` — `parse_side_line`, `RENAME_KEYWORDS`, `SIDE_KEYWORDS`, `MARKET_KEYWORDS`.

### Rename rules

#### `GLOBAL`
- **Form:** `GLOBAL FROM TO`
- **Meaning:** a plain rename in every stage (a security's true ticker). Between two bare crypto codes it is applied by the Coinbase and Kraken parsers before a row is read: it folds a staked or wrapped code into its coin (taxjson ships no such fold), or adds a Kraken legacy code.
- **Country:** both.
- **When:** a broker's odd spelling, a Questrade internal code the run could not resolve (the ATTENTION line gives the line), a coin alias.
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
- **Meaning:** two look-alike listings are separate securities (a CDR and its US share); changes no symbol, undoes an automatic cross-listing join and silences the scan's MAP-GAP nag. `taxjson journals` lists a journal between the two as refused, naming this line.
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
- **Meaning:** the Yahoo id of a coin whose ticker collides with another asset; fill-crypto, `taxjson crypto-sends` and `taxjson harvest` quote `YAHOO_ID-USD`. These lines are the only coin ids: without one a coin is quoted as `SYMBOL-USD`. A trailing `-USD` is accepted.
- **Country:** both.
- **When:** the run names a coin whose lookup failed or whose price is more than 5 times off its own trades.
- **Example:** `CRYPTO ZZC ZZC12345`

#### `EXTRACT`
- **Form:** `EXTRACT DESCRIPTION WORDS | CURRENCY | SYMBOL`
- **Meaning:** a parser symbol override: a broker row whose description contains the words (whole words, any case) and whose currency is CURRENCY (`*` = any) gets SYMBOL. First matching line wins; options and futures are never rewritten.
- **Country:** both.
- **When:** the currency-to-suffix rule mislabels a security (a TSX-only USD unit). `taxjson run` warns when a `.US` symbol carries such a unit beside another company ("names two securities") and `taxjson ticker-map --suggest` offers the line, its words the shortest run common to every description of the fund in the project and in no other row's (the words must match the broker's description as written: `US DLR` and `U S DLR` are different words).
- **Example:** `EXTRACT Sample US Dollar Fund | USD | ZZD.U.TO`

#### `T1135`
- **Form:** `T1135 SYMBOL COUNTRY`
- **Meaning:** the T1135 domicile of SYMBOL where its listing suffix is wrong: an ISO 3166 alpha-3 code, or `CA` / `CAN` / `CANADA` / `EXCLUDE` for not specified foreign property. It follows the symbol through a rename; any other value stops the report.
- **Country:** Canada (read by `taxjson t1135`).
- **Example:** `T1135 ZZQ.US CA`

### Market lists (extend or override the shipped data)

The lists taxjson cannot read from an export ship in `src/taxjson/data/markets.toml` (read only by `src/taxjson/lib/markets.py` — `data`, `overrides`). Whenever a built-in list decides an outcome the run prints one note per symbol naming the line that would change it. The file also states a listing convention: `[usd_unit_class]` makes a Canadian-listed fund's US-dollar units `ROOT.U.TO` (suggested for a symbol collision, and used for a Questrade currency journal's USD leg when the account's own rows show no USD listing of the security; an `EXTRACT` or `GLOBAL` line overrides it for one security; `src/taxjson/lib/markets.py` — `usd_unit_listing`). These ticker.map lines change one entry each:

#### `STABLE`
- **Form:** `STABLE SYMBOL USD` or `STABLE SYMBOL NO`
- **Meaning:** a US-dollar stablecoin, or not one. In a Canada project a stablecoin is US-dollar cash; in a US project it is property like any coin (a swap, reward or fee in one valued at its 1.00 USD par).
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

## .tt files

Hand-entered rows, any `*.tt` file in `inputs/<account>/`. One space-separated line per row; `#` starts a comment; numbers take a decimal point (a thousands comma is fine, a decimal comma is refused); symbols are upper-cased and carry their listing suffix (`.TO`, `.US`; options in OCC form). A line has one date, used as both trade and settle date: write the date that matches `tax_date`. Rows at one date and time are taken in file order. Validate a file with `taxjson-convert-tt --account margin inputs/margin/x.tt`. The parser is `src/taxjson/bin/taxjson_convert_tt.py` — `parse_tt_line`, `parse_opening_line`, `parse_inkind_line`, `parse_journal_line`, `parse_rename_line`, `expand_acquired`, `_VALID_ACTIONS`, `_SUGAR_ACTIONS`, `_EVENT_ACTIONS`. The dated events (`JOURNAL`, `RENAME`) are written date first with no time column; `taxjson run` books them (`src/taxjson/lib/dated_events.py` — `read_declarations`, `settle_journals`, `write_sidecars`) and records each in `work/dated_events.state` with its source (`tt`, `map`, `ib-conid`, `broker`) and place.

#### `BUYSELL`
- **Form:** `BUYSELL DATE TIME SYMBOL QTY CUR PRICE TOTAL [FEE] [xSIZE]`
- **Meaning:** a trade. QTY positive = buy, negative = sell; TOTAL is the net cash: buy = qty × price + fee, sell = qty × price − fee, written positive (a sale whose fee exceeds its gross has a negative total). A total more than 1% off qty × price ± fee is warned about. `xSIZE` is a contract size other than an equity option's 100 (`x1000`, `x50`).
- **Example:** `BUYSELL 2025-03-10 09:30:00 ZZQ.US 10 USD 45.00 459.95 9.95`

#### `ASSIGN`
- **Form:** as BUYSELL.
- **Meaning:** the two legs of an exercise or assignment, one line each: the option leg at price 0 and the stock leg at the strike; the premium folds into the shares' cost or proceeds. A written call assigned on 1 contract:
- **Example:** `ASSIGN 2025-06-20 16:00:00 ZZQ250620C00005000.US 1 USD 0 0` then `ASSIGN 2025-06-20 16:00:01 ZZQ.US -100 USD 5 500`

#### `TRANSFER`
- **Form:** `TRANSFER DATE TIME SYMBOL QTY CUR PRICE TOTAL [DECLARED]`
- **Meaning:** shares moved in (positive QTY) or out (negative). `DECLARED` marks a deliberate statement (a custody-move counter-leg), which the transfer checks accept.
- **Example:** `TRANSFER 2025-02-03 09:30:00 ZZQ.TO -20 CAD 30.00 600.00 DECLARED`

#### `ACQUIRED` (shorthand)
- **Form:** `ACQUIRED TRUE-DATE TIME SYMBOL QTY CUR PRICE TOTAL ARRIVED ARRIVAL-DATE`
- **Meaning:** shares that arrived by transfer, with their real purchase: it expands to a BUYSELL on the true date plus a DECLARED counter-TRANSFER on the arrival date.
- **Example:** `ACQUIRED 2019-05-10 09:30:00 ZZQ.TO 40 CAD 12.00 480.00 ARRIVED 2024-06-03`

#### `OPENING`
- **Form:** `OPENING SNAPSHOT-DATE SYMBOL QTY CUR TOTAL-COST [LOT-DATE]` (no time column)
- **Meaning:** an opening balance from a positions report: sets the position and its book cost on the snapshot day, is not a purchase, and replaces the account's earlier rows of that symbol. Long positions only. A US line needs its lot date and a USD cost. `taxjson opening` writes these lines.
- **Example:** `OPENING 2023-12-29 ZZB.TO 20 CAD 204.95`

#### `INKIND`
- **Form:** `INKIND DATE SYMBOL QTY CUR PRICE [TOTAL] [plan=KIND]` (no time column), in a taxable account's folder; or `INKIND DATE SYMBOL QTY plan=own` (no value).
- **Meaning:** declares and values an in-kind move between this taxable account and a registered plan (an RRSP or TFSA contribution, a withdrawal; US: an IRA distribution). QTY is signed as the shares move in this account: negative = out to the plan, positive = back from it. PRICE is the value per share in CUR; give `0` and a TOTAL to state the whole value. The line names this account's transfer row of that security and quantity nearest DATE (within 10 days) and books it as in kind, dated DATE: with the plan's transfer row of the same quantity (it settles an ambiguous pair the run would not book on its own; `plan=` picks the plan's leg), else the plan's rows that add up to it (a delivery in parts), else as a move to a plan outside the project — `plan=` names that plan (`rrsp`, `tfsa`, `ira` ...). `plan=own` declares the row a move of your own: never in kind, and it no longer makes another pair ambiguous or a delivery in parts suspect. `plan=` needs a value; a value that is not a finite amount is refused. It is never a row of the books; a line matching no transfer row is listed in the run's in-kind warning. The parser is `parse_inkind_line`.
- **Example:** `INKIND 2025-03-14 ZZQ.TO -100 CAD 15.00` (100 shares contributed at 15.00 each); `INKIND 2025-03-14 ZZQ.TO -100 plan=own` (that transfer-out was a move between your own accounts)

#### `JOURNAL`
- **Form:** `JOURNAL DATE FROM TO QTY` (no time column)
- **Meaning:** QTY units moved from listing FROM to listing TO of one security inside this account (a Norbert's gambit's journal, a TSX line moved to its NYSE line). Booked as the move's two transfer legs, kept with the account's transfer evidence (`work/<account>_tt-journal_transfers.json`), never a purchase or a sale: the two listings are joined as one security (as a ticker.map `TOBASE` line would; the base-currency listing is kept) when something shows they are listings of one security — one root (`ZZG.TO`, `ZZG.U.TO`, `ZZG.US`; a class or unit designator is part of the root) or names in the exports that agree as a broker journal's legs' names must, and no names of two different companies; otherwise the run stops, and a ticker.map `TOBASE FROM TO` line is the deliberate join (the line is then booked). The legs move units inside this account only (never paired with another account's transfer); the holdings view moves the units, the missing-history checks read the day as a journal. Both countries. When the broker's rows already hold both legs (the account's out-leg of FROM and in-leg of TO, the same quantity, within 5 business days) the line is a duplicate: an Info line, nothing booked twice; with one leg there, only the other leg is booked. A ticker.map rule naming either listing wins; a `DISTINCT` line keeps them apart (a Warning). Refused in a crypto account, for an option contract or a future (never journaled into shares), for a date in the future and for an implausible quantity (over a trillion units). Two identical lines (account, date, FROM, TO, quantity) are one journal, with a Warning: two journals of one size on one day are one line with the total. A line that moves MORE units than a journal the broker's rows already hold between the same two listings (within 5 business days) stops the run: write only the units the rows lack.
- **Example:** `JOURNAL 2025-03-05 ZZG.TO ZZG.U.TO 100`

#### `RENAME`
- **Form:** `RENAME DATE OLD NEW [late=fold|late=separate]` (no time column)
- **Meaning:** a ticker change on that date, booked as an event: the pool (US: the lots and holding periods) carries from OLD to NEW. Written in any account's .tt file, it applies to every account of the same kind (securities or crypto: a crypto account's line is a coin's ticker change) whose books hold OLD before the date — or a symbol an earlier change moved into OLD — and is recorded once. Every declaration of one change (`.tt` lines in several accounts, a legacy ticker.map line, within a week of each other) is one event dated the earliest (an Info line); the changes apply in date order. Refused, naming the lines: OLD renamed to another symbol within a week, one change on two dates further apart, a cycle (`A -> B` and `B -> A`), one account choosing both `late=` values. A trade in OLD after the change (later on its own day included) is listed by `taxjson renames` and stops `run --strict` until `late=fold` (book it as NEW) or `late=separate` (another company reusing the ticker). A line's `late=` applies to its own account's late rows and to every account without a line of its own; another account's own line may choose otherwise for its rows. IB's one contract id under two symbols is booked as this event without a line (a Warning names the `DISTINCT` and `late=separate` way out); a line for the change books it instead. A line dated in the future is refused, and so is one naming an option contract or a future: an option follows its underlying's ticker change (write the stock's line), and another expiry, strike or right is another contract. A line between two spellings of one listing in the books (`ZZA.TO` and `ZZA.CN`: a Canadian venue folds into `.TO`) books nothing, with an Info line.
- **Example:** `RENAME 2025-04-01 ZZOLD.US ZZNEW.US late=fold`

#### `SPLIT`
- **Form:** `SPLIT DATE TIME OLD NEW RATIO`
- **Meaning:** a split or consolidation (RATIO new shares per old: `2`, or `0.1` for one-for-ten) or, with RATIO `1` and a new symbol, a dated rename.
- **Example:** `SPLIT 2025-05-01 09:30:00 ZZOLD.US ZZNEW.US 1`

#### `ADJUST`
- **Form:** `ADJUST DATE TIME SYMBOL CUR AMOUNT [type=roc|dist] [record=YYYY-MM-DD]`
- **Meaning:** a cost adjustment: negative lowers the ACB/basis (a return of capital, T3 box 42), positive raises it (a stock dividend's declared amount, a notional distribution). On a short position it changes the cover's gain.
- **Example:** `ADJUST 2025-12-31 16:00:00 ZZF.TO CAD -12.50 type=roc`

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

---

## missing_history.json

At the project root (the old name `phantoms.json` is still read, with a note; both names at once is refused). A JSON array of `{ "symbol", "account" }` objects; keys starting with `_` are ignored. Each entry gives the account a missing-history opening with no cost: sales drawing on it are listed for manual reporting and left out of the totals. `taxjson find-missing-history --write-missing-history` writes candidates (never over an existing file without `--force`). An entry whose position no longer goes short is reported STALE; delete it. Both countries. Read by `src/taxjson/lib/missing_history.py` — `load_missing_history`, `project_missing_history_file`.

```json
[
  {"symbol": "ZZQ.US", "account": "margin", "_note": "bought before 2019"}
]
```

When to use it: only for what is left after importing the real purchases (`.tt` BUYSELL lines, an opening balance).

---

## Generic importer mapping

For a broker taxjson has no parser for: name the export `generic_<anything>.csv` and describe its columns in `generic_<anything>.csv.toml` (wins) or one shared `generic.toml` in the same folder. Unknown sections and keys are refused with a did-you-mean. Securities only (no crypto). Both countries. Parser: `src/taxjson/lib/brokerages/generic.py` — `_load_mapping`, `_COLUMN_KEYS`, `_FORMAT_KEYS`, `_DEFAULT_KEYS`, `_BROKER_KEYS`, `_OPTIONS`, `_VALID_TARGETS`. Template: `examples/generic_wealthsimple.toml`.

| Section | Keys | Meaning |
| --- | --- | --- |
| `[columns]` | `date` (required, the trade date), `settle`, `action`, `symbol`, `quantity`, `price`, `amount` (fee-inclusive signed total), `fee`, `currency`, `account` | CSV header names, case-insensitive |
| `[formats]` | `date`, `settle` (strptime, default `%Y-%m-%d`), `tax_sign` (`cash` or `withheld`), `fee_sign` (`cash` or `charged`) | how cells are written |
| `[actions]` | CSV value = `buy`, `sell`, `dividend`, `dividend_in_lieu`, `tax`, `interest`, `fee` or `skip` | the action map; an unmapped value carrying money is UNBOOKED |
| `[defaults]` | `currency`, `action`, `symbol` | values for columns the CSV lacks |
| `[options]` | `allow_large_fees` (a fee over 5% of the gross is refused unless true), `settle_on_trade_date` (the date column is already the settle date) | switches |
| `[broker]` | `name` (the real broker, lower case), `account` (the broker account every row belongs to) | labels |

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

Every buy/sell row is cross-checked (amount vs qty × price ± fee within 1%, duplicate column names, signs, a settle date more than 31 days after the trade); the import refuses rather than guess. There is no assignment target: book exercises as `.tt` ASSIGN lines.

---

## Holdings TOML

A broker's positions on a date, read by `taxjson sanity` (and `[accounts.NAME] holdings`) and `taxjson opening`; `taxjson fetch --positions` writes one for Questrade. Never booked by `taxjson run`. Both countries. Reader: `src/taxjson/lib/positions_reports.py` — `_read_toml`.

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

`sends.json` holds your decision for each crypto send that did not arrive in another of your crypto accounts; `taxjson crypto-sends --set ID=DECISION` writes it. Commit it. `crypto_sends.tt` is generated from it (one sale at fair value per gift or payment, and per network fee): never edit it; a hand-written file of that name is never overwritten. Reader: `src/taxjson/lib/crypto_sends.py` — `load_decisions`, `record_decision`, `DECISIONS`, `REFUSED`.

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

Written by `taxjson close-year`: the closed year's sales, year-end positions and cost, its country and date basis, and the carry-forwards (net capital loss or US short/long-term carryover; Canada's minimum tax carryover). Read by `taxjson check-filed`, `taxjson handoff`, `taxjson carryover`, `taxjson option-boundary` and the next year's estimate (through `prior_year_record`). Commit it; do not edit it. A lock closed under the other country's rules is refused.

---

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

## Old files and environment variables

- Old per-purpose files are no longer read and stop every command until `taxjson migrate` folds them in: `yf_ticker.map` (QUOTE lines), `crypto_ticker.map` (CRYPTO), `ticker_extraction_overrides.txt` (EXTRACT), `t1135.map` (T1135), `amt_carryover.txt` (`[estimate] amt_carryover`), `claimed_losses.txt` (`[carryover] claimed`), `capital_gains_dividends.map` (`[[capital_gains_dividends]]`), `distributions.map` (`[[distributions]]`). `tv_exchange.map` is only renamed. Code: `src/taxjson/lib/migrate.py` — `legacy_files`.
- `TAXJSON_LOCAL_TZ`: the crypto time zone outside a project (the setting wins).
- `TAXJSON_TICKER_MAP`: set by `taxjson` for every stage; the ticker.map a stand-alone tool reads.
- `TAXJSON_WIDTH`: the column width text wraps at, for a person. Unset: the terminal's full width (at most 160, never under 40), or 120 when the output is piped or redirected; `0`: no wrapping (what the run writes to `work/` and `reports/` is always unwrapped). Code: `src/taxjson/lib/out.py` — `width`.
