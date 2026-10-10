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

## Irish-domiciled ETFs and listings outside the currency table get the wrong suffix

- **What happens:** an Interactive Brokers dividend or withholding row is given its listing from the
  security's ISIN country, and every Irish (`IE`) ISIN maps to `.L` (London), though many Irish ETFs
  trade elsewhere. When the account holds the security under another listing, the income is moved to
  that holding (so most books are not affected); an income-only security keeps `.L`. Separately, a row
  that names only a currency outside the shared table (CAD, USD, AUD, GBP) gets the currency code as its
  suffix (`SAMPLE.EUR`; the IB parser warns), and a USD security listed outside the US gets `.US` unless
  the export says otherwise.
- **Meanwhile:** add a ticker.map `GLOBAL` line that rewrites the parsed symbol to the right listing.
- **Code:** `src/taxjson/lib/brokerages/ib_extractor.py` — `_isin_ext`, `_reattribute_income_to_holdings`; `src/taxjson/data/markets.toml` — `isin_country_suffix`, `currency_suffix`

## Opening balances: edges the cut-off does not see

- **What happens:** an opening snapshot (`tjs opening`) replaces the account's earlier rows of its
  symbols, but the cut-off runs at the merge stage, so two later additions dated before the snapshot are
  not left out: a `[[distributions]]` cost adjustment, and a missing-history `OPENING … cost=unknown`
  line. One account holding the same symbol at two brokers with different snapshot dates cannot be
  opened per broker (one snapshot date per symbol per account). A US project asks for a lot date on every
  OPENING line, a retirement account's included, where the holding period does not matter.
- **Meanwhile:** remove such an adjustment or opening line, or date it after the snapshot; give the two
  brokers' holdings of one symbol a common snapshot date; give a retirement account's lines any lot date.
- **Code:** `src/taxjson/lib/opening.py` — `snapshots`

## A negative futures price in a generic or `.tt` file

- **What happens:** IB futures rows keep the sign of a negative price, but a generic-importer futures row
  at a negative price is read as its magnitude, so its P/L sign is wrong. In a `.tt` file a negative-price
  buy keeps its signed total; a negative-price sell with a negative total is accepted only with its
  contract size on the line and refused otherwise; a negative price with a positive total is not caught.
- **Meanwhile:** book such a fill from the IB statement, or as a `.tt` line with the signed total (and
  the contract size on a sell), or enter the close's realized P/L by hand.
- **Code:** `src/taxjson/lib/brokerages/generic.py` — `_trade_net`; `src/taxjson/bin/taxjson_convert_tt.py` — `_excess_commission_sale`

## Kraken fees taken in the traded coin are not in the fee reports

- **What happens:** when Kraken takes a fee in the coin traded, the parser folds it into the quantity
  (fewer coins received, more given), so cost and proceeds are right, but the row's fee is 0 and
  `fees.rpt`, `tjs fees-sum` and the `.sum` FEES line leave it out. These can be most of a Kraken
  account's trading fees. The parse note says so.
- **Meanwhile:** the gains are right; for a fee total, add the coin fees from the Kraken ledger.
- **Code:** `src/taxjson/lib/brokerages/kraken.py` — `_parse_trades`; `src/taxjson/bin/taxjson_fees.py`

## A stock dividend received while short covers part of the short

- **What happens:** a stock dividend paid while the account holds an open short is booked like any $0
  purchase, so it covers part of the short at $0 (a gain of the short's per-share proceeds). In fact the
  short seller owes the lender the new shares: the short grows and its proceeds are spread over more
  shares. In a US project the warning then says "no shares held". Both countries.
- **Meanwhile:** replace the row in a `.tt` file with the entry that matches the broker's statement (for
  example a $0 short sale of the new shares).
- **Code:** `src/taxjson/lib/core.py` — `STOCK_DIVIDEND`

## Second-order superficial losses from the ACB bump's date

- **What happens:** the denied loss is added to the replacement shares on the date of the purchase that
  triggered it. With a rebuy, a partial sale inside the window and the rest sold later, the inner sale
  takes part of that amount and can itself be denied and deferred again, whereas CRA's guide (T4037)
  puts the whole denied amount on the shares still held at day 30. Year totals agree unless the two
  sales fall in different years; the extra DISALLOW row shows in `tjs wash-sales`.
- **Meanwhile:** when the two sales fall in different years, check the split in `tjs wash-sales` and
  correct it by hand.
- **Code:** `src/taxjson/lib/core.py`

## Margin interest paid is netted against interest earned

- **What happens:** IB `INTEREST` rows keep their sign, and the income summary nets interest paid
  against interest earned, so margin interest (deductible as a carrying charge, line 22100) shrinks the
  interest income instead of being shown as a deduction. `tjs estimate` does not read interest paid from
  the books.
- **Meanwhile:** take interest earned and paid from the broker's statement or slip, and enter the year's
  carrying charges with `tjs estimate --carrying-charges` (or `[estimate] carrying_charges`).
- **Code:** `src/taxjson/bin/taxjson_sum_income.py`

## Limitations and things taxjson does not do

Messages that say "not modeled — KNOWN_ISSUES" refer to a limitation by design. Limitations and things
taxjson does not do: [docs/limits.md](docs/limits.md).
