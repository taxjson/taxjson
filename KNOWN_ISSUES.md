# Known issues

Open bugs in the current release: behaviour that is meant to work and does not, each with what to do
until it is fixed. A fixed bug leaves this file, and the CHANGELOG names the release that fixed it.

- Things taxjson does not do by design (rules not modelled, data the exports do not carry): see
  [docs/limits.md](docs/limits.md).
- A message you see, with how to check it and the fix: see
  [docs/troubleshooting.md](docs/troubleshooting.md).
- Found a bug that is not listed? Open an issue with the
  [bug report template](.github/ISSUE_TEMPLATE/bug_report.md). The repository is public: reproduce it
  on a made-up file, never paste a real export, amounts or account numbers.

## Listings outside the currency table get the currency's code as their suffix

- **What happens:** an Interactive Brokers row that names only a currency outside the shared table
  (CAD, USD, AUD, GBP) gets the currency code as its suffix (`SAMPLE.EUR`; the IB parser warns), and
  a USD security listed outside the US gets `.US` unless the export says otherwise. A dividend or
  withholding row is given its listing from the security's ISIN country only where that country has
  a home market in the table (`CA`, `US`, `GB`, `AU`); when the account holds the security under
  another listing the income is moved to that holding. A row of any other ISIN country (an Irish
  fund's) is booked on the listing the account holds, a ticker.map line names or the statement's
  instrument list gives, and the parse stops when nothing settles one. A row whose Description
  carries no ISIN is booked on `.US`.
- **Meanwhile:** add a ticker.map `GLOBAL` line that rewrites the parsed symbol to the right listing.
- **Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `_isin_ext`, `_reattribute_income_to_holdings`, `_ib_settle_assumed_listings`; `src/taxjson/data/markets.toml` — `isin_country_suffix`, `isin_domicile_suffix`, `currency_suffix`

## Opening balances: edges the cut-off does not see

- **What happens:** an opening snapshot (`tjs opening`) replaces the account's earlier rows of its
  symbols. (A `[[distributions]]` return of capital dated before it is not applied, as ATTENTION:
  the snapshot's cost stands for it.) One account holding the same symbol at two brokers with
  different snapshot dates cannot be opened per broker (one snapshot date per symbol per account). A
  US project asks for a lot date on every OPENING line, a retirement account's included, where the
  holding period does not matter.
- **Meanwhile:** give the two brokers' holdings of one symbol a common snapshot date; give a
  retirement account's lines any lot date.
- **Code:** `src/taxjson/lib/opening.py` — `snapshots`

## Kraken fees taken in the traded coin are not in the fee reports

- **What happens:** when Kraken takes a fee in the coin traded, the parser folds it into the quantity
  (fewer coins received, more given), so cost and proceeds are right, but the row's fee is 0 and
  `fees.rpt`, `tjs fees-sum` and the `.sum` FEES line leave it out. These can be most of a Kraken
  account's trading fees. The parse note says so.
- **Meanwhile:** the gains are right; for a fee total, add the coin fees from the Kraken ledger.
- **Code:** `src/taxjson/lib/brokerages/kraken.py` — `_ledger_trade_legs`; `src/taxjson/bin/taxjson_fees.py`

## A stock dividend received while short: `tjs find-missing-history` still lists it as shares at $0

- **What happens:** the gains run and its loss rules raise the short by the new shares (tax-logic
  US-STKDIV-06, CA-STKDIV-02). `tjs find-missing-history` and the run's closing "position at a $0
  cost" line add up the rows themselves and still list the row as shares held at $0 cost. A short
  covered between the ex-dividend date and the pay date leaves the row booked as a $0 purchase.
- **Meanwhile:** the books are right in the first case: ignore the two lines for that row. In the
  second, replace the row in a `.tt` file with the entry that matches the broker's statement.
- **Code:** `src/taxjson/lib/missing_history.py` — `detect_zero_basis_acquisitions`; `src/taxjson/lib/row_types.py` — `STOCK_DIVIDEND`

## Limitations and things taxjson does not do

Messages that say "not modeled — KNOWN_ISSUES" refer to a limitation by design. Limitations and things
taxjson does not do: [docs/limits.md](docs/limits.md).
