# rbc-options: an RBC Direct Investing options account

Synthetic: every row, price, quantity and account number is made up. Canada, tax year 2025,
history from 2024; `option_grant_timing_since = 2024` (every premium taxed in the year written);
`tax_date = "settle"`; `fx_cash_ledger = "v2"`. Two made-up underlyings: ZZQV (a TSX share, CAD,
options written `.ZZQV`) and ZZRW (a US share, USD). USD is at the synthetic 1.35 every day: the
golden harness seeds one rate file for every book (tests/_hermetic.py `home_env`), so a varying
rate cannot be put in this book. Commissions, made up (not RBC's schedule): 8.00 a share order
(once, on its first fill), 6.25 + 1.50 a contract a CAD option order (a fill after the first: 1.50
a contract), 5.00 a USD option order, 55.00 an assignment's share leg (at the strike). A trade
row's Value is |quantity| x price (x 100 for an option), less its commission on a sale and plus
it on a purchase.

Every money figure is built so that one of 100.00 or more ends in .00, .25, .50 or .75 (the
privacy check, `scripts/check-pii.sh`, lists no such figure): every CAD amount is a quarter and
every USD one a multiple of 5.00 (a quarter at 1.35); the 300 shares' ACB is a multiple of 3.00
and each later share purchase whole dollars, so every per-share ACB has two places. Keep it so
when a row changes. For the same reason the share sales' average holding period in the `.sum`
is a whole 221.0 days (the superficial-loss shares are bought 06-10).

## The exports

| File | What it is |
|---|---|
| `inputs/margin/rbc_2024.csv` | 2024, "Activity Export as of Jan 10, 2025" |
| `inputs/margin/rbc_2025.csv` | 2025, "Activity Export as of Feb 3, 2026" (its rows end 2025-12-29) |
| `inputs/margin/rbc_2026.csv` | the start of 2026, taken the same day: one interest row (2026-02-02) |
| `inputs/margin/rbc_2025_partial.csv` | a summer download, "Activity Export as of Aug 8, 2025", its rows 2025-06-20 to 2025-08-06: every row also in `rbc_2025.csv`; the parser skips them as an overlapping download of the same account |
| `inputs/margin/fx.tt` | what the exports do not carry: the CAD/USD conversion (`FXCONV`), no USD held on 2025-01-01 (`CASHOPEN`), the year-end USD balance (`CASHBAL`) |
| `inputs/tfsa/rbc_tfsa_2025.csv` | the TFSA (sheltered), 2025, "Activity Export as of Feb 3, 2026" |
| `holdings/margin_holdings.csv`, `holdings/tfsa_holdings.csv` | RBC Holdings Exports at 2025-12-31, for `tjs sanity` |

RBC's activity layout (tests/_support/rbc_rows.py `HDR`): every cell quoted, long dates ("June
20, 2025"), an Account column (99900099 margin, 99900000 TFSA), `Symbol Description`, one
Value column (net of the commission); no "Date Range:" line (RBC writes none: the as-of date
bounds what a file holds). Option symbols are 8xxxxxx for a CAD option, 9xxxxxx for
a USD one; the 2025 export's option descriptions read as RBC's do ("PUT ZZRW   01/16/26   20.00
ZZRW HOLDINGS INC UNSOLICITED CA OPEN CONTRACT"), the 2024 and TFSA exports' with single
spaces. Each export lists its rows newest first. Dates are trading days, expiries third
Fridays, options settle T+1; an expiry row is posted the next business day; an assignment's
ASN row and share leg are dated the expiry Friday and settle two business days later.

## The rows, by what they test (margin unless named)

- **Cash.** A 4850.00 CAD deposit (2024-06-28); the CAD/USD conversion that funds the put
  (`fx.tt`, 2025-05-30: 2760.75 CAD for 2045.00 USD); interest (4.24, 2025-12-15; 2.56 on
  2026-02-02, 2026 income in `rbc_2026.csv`: the checklist's inputs-frozen wants a row after
  January, TJ-5388); a ZZRW dividend of 35.00 USD with 5.00 USD withheld (2025-09-15). FX on
  foreign cash: computed by ledger v2, 0.00 (every flow at 1.35); the year-end balance
  reconciles (385.00 USD).
- **Shares bought, then covered calls written monthly (2024).** 2024-07-02: 300 ZZQV bought in
  one order filled 100 at 12.08 + 200 at 12.10 (the commission on the first fill: ACB 3636.00,
  12.12 a share), then 2 calls written. Each month after, the expired call's EXP row (+2) and the
  next write. 2024's premiums: 187.25.
- **A call written in December, assigned in January (s.49(4)).** 2024-12-23: 1 ZZQV
  2025-01-17 12.50 call written at 0.31 (23.25 net); assigned 2025-01-17, 100 shares called
  away at 12.50. The premium leaves 2024 (a year-2024 run of these inputs shows 187.25, not
  210.50) and joins the 2025 sale's proceeds: 1250.00 + 23.25 - 55.00 - 1212.00 = 6.25.
  Schedule 3 shows the premium gross, 31.00, in the proceeds (1281.00) and the write's 7.75
  among the outlays (62.75): the same gain (CA-OPT-06; the owner's ruling, backlog TJ-5381).
  `tjs option-boundary --details` names the 2024 amendment, if 2024 was filed with it.
- **A trade settling across the year end.** 2024-12-31: the 2 Jan 13 calls written 12-23 bought
  back at 0.04 (17.25), settled 2025-01-02: a 2025 loss under `tax_date = "settle"`.
- **Partial fills of one option order.** 2025-01-20: 2 ZZQV Feb 13 calls written as two fills
  of 1 at 0.25 (7.75, then 1.50), bought back in the first roll as two fills of 1 at 0.54.
- **Two rolls, listed both ways.** 2025-02-18: the Feb calls bought back, 2 Mar 13.50 calls
  written (the write listed above); 2025-03-17: the Mar calls bought back (listed above), 2 May
  13.50 calls written as two fills of 1 at 0.33 (7.75, then 1.50). Each roll spans two series,
  so nothing is reordered and no note is written; the rolls move no figure under grant timing
  (each write and each buy-back is its own disposition).
- **Calls assigned, the shares called away.** 2025-05-16: the May 13.50 calls' ASN row (+2) and
  200 ZZQV sold at 13.50, settled 05-21 (after Victoria Day): 2700.00 + 56.75 - 55.00 -
  2424.00 = 277.75 (s.49(3)). Schedule 3 shows the premiums gross, 33.00 + 33.00, in the
  proceeds (2766.00) and the writes' 7.75 + 1.50 among the outlays (64.25).
- **A cash-secured put written and assigned.** 2025-06-02: 1 ZZRW 2025-06-20 20 put written at
  0.40 (35.00 USD net); assigned, 100 ZZRW bought at 20.00 + 55.00. The premium lowers the
  shares' ACB: 2055.00 - 35.00 = 2020.00 USD (2727.00 CAD).
- **A superficial loss.** 100 ZZQV bought 2025-06-10 at 14.03 (1411.00), sold 11-03 at 13.21
  (1313.00), 100 bought back 11-17 at 13.07 and held at the end: the 98.00 loss is denied and
  added to the new shares' ACB (1315.00 + 98.00 = 1413.00).
- **Covered calls written monthly on the put's shares.** 06-23 to 11-24: one ZZRW 21.50 call a
  month, each expiring worthless; 12-22: a LEAPS call (2028-01-21 25) written at 1.10, open at
  the end.
- **A long call bought and sold.** 2 ZZQV Oct 14.50 calls: bought 08-05 at 0.34 (77.25), sold
  09-15 at 0.69 (128.75): gain 51.50.
- **A long put that expires worthless.** 4 ZZRW Sep 19 puts bought 08-06 at 0.05 (25.00 USD);
  its EXP row 09-22: a loss of 33.75 on the expiry date, 09-19.
- **A same-day write and buy-back of one series, the OPEN row listed above the CLOSE
  (TJ-5348).** 2025-12-29: 2 ZZRW 2026-01-16 20 puts written at 0.60, listed above their
  buy-back at 0.40. The export's as-of date (2026-02-03) reaches past the expiry, so the parser
  puts the write first and says so in the account's `.sum`: no ATTENTION, nothing left open.
- **The TFSA.** 2025-01-31: a 1300.00 contribution (its cash never below zero). 2025-02-03: 100
  ZZQV bought (1282.00) and 1 ZZQV Mar 13.50 call written (21.25), the series the margin account
  writes on 02-18; the TFSA's expires while the margin's is bought back: each account's write is
  closed only by its own rows (CA-OPT-12). Sheltered: out of the filing totals.
- **The Holdings Exports.** Quantities agree (`tjs sanity`: OK). RBC's book costs differ from the
  books where the tax rules move them: ZZQV by the denied loss (+98.00, named), ZZRW by the
  put's premium (-35.00 USD, which sanity calls unexplained: backlog TJ-5385).

## The answers (hand-computed, pinned by tests/features/gains_run/test_rbc_covered_call_account_figures.py)

2025, margin: shares 284.00 (line 13199/13200: proceeds 5368.00, ACB 4949.00, outlays 135.00;
denied 98.00); options 428.75 (line 15199/15300: proceeds 908.00, ACB 397.50, outlays 81.75);
the RETURN 712.75; dividend 47.25, tax withheld 6.75, interest 4.24. 2024 (a year-2024 run):
options 187.25. The TFSA: 21.25, shares held at 1282.00.

No money amount sits on a half cent: every amount is a quarter, so every sum is a quarter (and
exact as a float) and each Schedule 3 row foots on its exact gain; the day trade's row is
162.00 - 114.75 - 6.75 = 40.50. The half-cent case of backlog TJ-5056 is pinned by its own
repro, not here. Two printed averages do sit on a half cent, exact as floats (sums of quarters
over 10 and over 4 trades): the margin `.sum`'s options Average Positive 55.625 and Average
Negative -31.875, shown 55.63 and -31.88 (half-up; half-even would show 55.62 for the first).
The fees report's MEDIAN is 7.75 (the 15th of 29 fee rows, one row's fee). Not money: ZZQV's
AVG_DAYS in the `.sum` is 99.6 days; five of the traces' four-place per-share figures are a
cent amount over 200 shares that ends on a half of the fourth place. Three are shown where
half-even rounding of the exact value would differ: the Gain/Sh of the 2024-12-31 buy-back
(-17.25, shown -0.0863) and of the 2025-03-17 one (-37.25, -0.1863), and the ACB/Sh of the long
call (77.25, 0.3863), which half-even would give as -0.0862, -0.1862 and 0.3862. Two are the
same either way: the Gain/Sh of the May sale (277.75, 1.3888) and of the day trade's buy-back
(-114.75, -0.5738).
