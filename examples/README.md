# Examples

Synthetic data you can run end-to-end against each supported broker parser. All numbers are fabricated — no real account information.

To try taxjson on a whole project instead, `tjs init --demo ~/taxjson-demo` makes one from these files (tax year 2024, Canada) and prints the commands to run.

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
`inputs/<account>/<file>.csv.toml` (a sidecar for that one file, any file name)
or as `inputs/<account>/generic.toml` (shared by every `generic_*.csv` in the
folder) and adjust the header names and action values to match your CSV. See
[docs/brokers.md, "Any other broker"](../docs/brokers.md#any-other-broker-generic-importer)
and the mapping reference in
[docs/settings.md](../docs/settings.md#generic-importer-mapping).

## Try them in a project

The quickest way to see taxjson work is a project: `tjs init`, copy a demo CSV
into an account folder of `inputs/`, then `tjs run` and `tjs sum` in the year
folder ([docs/getting-started.md](../docs/getting-started.md)).

## Manual pipeline (stage by stage)

The stage tools run one file at a time, without a project — useful to see what
a parser makes of a file. Run them from the repository root:

```bash
BROKER=questrade        # ib | rbc_direct | webull | kraken | coinbase | questrade
COUNTRY=ca              # ca | us
export TAXJSON_LOCAL_TZ=America/Toronto   # Kraken / Coinbase: UTC times are dated in this zone (required)
export TAXJSON_OFFLINE=1                  # optional: no network (the demos need none)

# 1. Parse to normalized JSON
taxjson-brokerage --brokerage $BROKER --account demo --country $COUNTRY \
    examples/${BROKER}_demo.csv > /tmp/${BROKER}.json

# 2. Merge + dedupe + validate (--dedup/--validate are opt-in flags)
#    (with --to/--rates to convert currency, also pass --country $COUNTRY:
#    futures follow the country's lot rule)
taxjson-merge2 --dedup --validate /tmp/${BROKER}.json > /tmp/${BROKER}_merged.json

# 3. Compute gains for the tax year. --taxable turns on the superficial-
#    loss (ca) / wash-sale (us) rules — leave it out only for a registered
#    account, where they do not apply the same way. Under --country ca the
#    engine notes that it uses close timing for written options unless
#    --option-premium-timing grant is given (a project uses grant timing).
taxjson-gains --country $COUNTRY --year 2024 --taxable \
    /tmp/${BROKER}_merged.json > /tmp/${BROKER}_gains.json

# 4. Summarize
taxjson-sum-gains /tmp/${BROKER}_gains.json
taxjson-sum-income /tmp/${BROKER}_merged.json
```

## What each fixture exercises

### `questrade_demo.csv`
Two AAPL buys (100 @ 185, 50 @ 170), two AAPL sells (75 @ 200, 75 @ 180), one AAPL dividend ($18), and an open SHOP.TO position. Demonstrates ACB averaging across two lots and a mixed gain/loss disposition pattern.

### `ib_demo.csv`
MSFT round-trip (50 @ 400 → 50 @ 440), plus an NVDA loss followed by repurchase within 30 days (10 @ 148 in July 2024, after NVDA's June 10:1 split → -10 @ 140 → 10 @ 135). Step 3 passes `--taxable`, which is what turns on the loss-denial rules: the 82.00 USD loss is denied (§1091 wash sale under `--country us`, superficial loss under `--country ca`). Without `--taxable` the loss is allowed in full. Also includes two MSFT dividends with US withholding-tax rows.

### `rbc_direct_demo.csv`
RY.TO position built in two lots (100 @ 130, 50 @ 140), partial sale (100 @ 165), plus an ENB.TO open position. Two dividends include the "ON N SHS … PER SHARE" pattern so the parser extracts per-share rate and quantity.

### `kraken_demo.csv`
BTC and ETH trades using Kraken's `XXBT/ZUSD` and `XETH/ZUSD` pair notation with the historical X/Z asset-code prefixes. Demonstrates the asset-name normalization (`XXBT` → `BTC`).

### `coinbase_demo.csv`
BTC and ETH buys/sells plus two `Staking Income` rows. Each staking row produces a paired `DIVIDEND` (income at FMV) and zero-net `BUYSELL` (adds tokens to the inventory pool at cost basis = FMV).

### `webull_demo.csv`
AAPL position built in two lots (100 @ 185, 50 @ 170), partial sell (75 @ 200), an ABCD call option round-trip (buy 10 @ 1.55 → sell 10 @ 2.50), and a CAD-listed SHOP buy. Exercises Webull's day-first dates (`%d-%m-%Y`), the `@` symbol prefix, the `(parentheses for negative)` Proceeds format, the option-symbol reconstruction from `CALL ABCD02/19/27 45`, and the currency→exchange suffix mapping (`USD`→`.US`, `CAD`→`.TO`).

## Expected output

Spot-checking the Questrade fixture under `--country ca` (the end of
`taxjson-sum-gains`; the demo's amounts are made up):

```
TOTAL REALIZED GAIN:                 1,480.20 USD   # pii-ok (synthetic demo)
TOTAL DIVIDENDS / STAKING:              18.00 USD
GRAND TOTAL (GAIN+DIV+PIL):          1,498.20 USD   # pii-ok (synthetic demo)
TOTAL TRADING FEES PAID:                19.80 USD
```

Other fixtures produce comparable summaries — run the pipeline above and read
the `TOTAL REALIZED GAIN` line. The same dataset under `--country us` may
produce different realized-loss figures when the disposition pattern overlaps a
wash-sale window (§1091) versus a Canadian superficial-loss window (s.54,
s.40(2)(g)).
