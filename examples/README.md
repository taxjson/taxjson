# Examples

Synthetic data you can run end-to-end against each supported broker parser. All numbers are fabricated — no real account information.

| File                       | Broker               | Asset class    | Suggested country flag |
| -------------------------- | -------------------- | -------------- | ---------------------- |
| `questrade_demo.csv`       | Questrade            | US + CA stocks | `ca`                   |
| `ib_demo.csv`              | Interactive Brokers  | US stocks      | `us`                   |
| `rbc_direct_demo.csv`      | RBC Direct Investing | CA stocks      | `ca`                   |
| `kraken_demo.csv`          | Kraken               | Crypto         | `ca` or `us`           |
| `coinbase_demo.csv`        | Coinbase             | Crypto         | `ca` or `us`           |
| `webull_demo.csv`          | Webull               | US + CA stocks + options | `ca` or `us` |
| `generic_wealthsimple.toml` | any (generic importer) | column-mapping template | — |

## Generic importer template

`generic_wealthsimple.toml` is a starting mapping for the generic column-mapped
importer (brokers without a dedicated parser). Copy it next to your export as
`inputs/<account>/generic_<name>.csv.toml` (sidecar) or
`inputs/<account>/generic.toml` (whole folder) and adjust the header names and
action values to match your CSV. See "Any other broker" in the top-level README.

## Manual pipeline (stage by stage)

```bash
BROKER=questrade        # ib | rbc_direct | webull | kraken | coinbase | questrade
COUNTRY=ca              # ca | us

# 1. Parse to normalized JSON
taxjson-brokerage --brokerage $BROKER --account demo \
    examples/${BROKER}_demo.csv > /tmp/${BROKER}.json

# 2. Merge + dedupe + validate (--dedup/--validate are opt-in flags)
taxjson-merge2 --dedup --validate /tmp/${BROKER}.json > /tmp/${BROKER}_merged.json

# 3. Compute gains for the tax year
taxjson-gains --country $COUNTRY --year 2024 \
    /tmp/${BROKER}_merged.json > /tmp/${BROKER}_gains.json

# 4. Summarize
taxjson-sum-gains /tmp/${BROKER}_gains.json
taxjson-sum-income /tmp/${BROKER}_merged.json
```

## What each fixture exercises

### `questrade_demo.csv`
Two AAPL buys (100 @ 185, 50 @ 170), two AAPL sells (75 @ 200, 75 @ 180), one AAPL dividend ($18), and an open SHOP.TO position. Demonstrates ACB averaging across two lots and a mixed gain/loss disposition pattern.

### `ib_demo.csv`
MSFT round-trip (50 @ 400 → 50 @ 440), plus an NVDA loss followed by repurchase within 30 days (10 @ 850 → -10 @ 140 → 10 @ 135). Under `--country us` this exercises the §1091 wash-sale matcher. Also includes two MSFT dividends with US withholding-tax rows.

### `rbc_direct_demo.csv`
RY.TO position built in two lots (100 @ 130, 50 @ 140), partial sale (100 @ 165), plus an ENB.TO open position. Two dividends include the "ON N SHS … PER SHARE" pattern so the parser extracts per-share rate and quantity.

### `kraken_demo.csv`
BTC and ETH trades using Kraken's `XXBT/ZUSD` and `XETH/ZUSD` pair notation with the historical X/Z asset-code prefixes. Demonstrates the asset-name normalization (`XXBT` → `BTC`).

### `coinbase_demo.csv`
BTC and ETH buys/sells plus two `Staking Income` rows. Each staking row produces a paired `DIVIDEND` (income at FMV) and zero-net `BUYSELL` (adds tokens to the inventory pool at cost basis = FMV).

### `webull_demo.csv`
AAPL position built in two lots (100 @ 185, 50 @ 170), partial sell (75 @ 200), an ABBV call option round-trip (buy 10 @ 1.60 → sell 10 @ 2.50), and a CAD-listed SHOP buy. Exercises Webull's day-first dates (`%d-%m-%Y`), the `@` symbol prefix, the `(parentheses for negative)` Proceeds format, the option-symbol reconstruction from `CALL ABBV01/17/25 190`, and the currency→exchange suffix mapping (`USD`→`.US`, `CAD`→`.TO`).

## Expected output

Spot-checking the Questrade fixture under `--country ca`:

```
TOTAL REALIZED STOCK GAIN:           1,480.20 USD
TOTAL REALIZED DIVIDENDS:               18.00 USD
GRAND TOTAL REALIZED GAIN:           1,498.20 USD
```

Other fixtures produce comparable summaries — run the pipeline above and read the `taxjson-sum-gains` line for the total. The same dataset under `--country us` may produce different realized-loss figures when the disposition pattern overlaps a wash-sale window (§1091) versus a Canadian superficial-loss window (s.40(2)(g)).
