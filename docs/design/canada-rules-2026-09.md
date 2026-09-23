# Canada rules — design for the September 2026 changes

Three engine changes and one command, designed against a full impact
study of the code and an adversarial audit of the Canada engine against
the Act. Every consumer of option gains was read before this was
written; the impact list is at the end.

## 1. Option premium timing (ITA s.49)

**Rule.** Granting an option is a disposition of property with a nil
cost: the premium is a capital gain in the year the option is written
(s.49(1)). A later closing purchase is a capital loss in the year it is
made (IT-479R para 24). If the option is exercised or assigned, s.49(1)
is deemed never to have applied: the premium is folded into the share
leg (s.49(2)–(3)) and the grant year is amended (s.49(4)). Expiry adds
nothing — the grant-year gain stands.

**Today.** A written option is a short pool of its OCC symbol; nothing
is recognised until the position closes. Same-year round trips give the
same totals; year-straddling contracts put the premium in the wrong
year. This is the US §1234 convention and the US engine keeps it.

**Setting.**

```toml
[settings]
option_premium_timing     = "grant"   # grant (statutory, default) | close (net at close)
option_grant_timing_since = 2025      # contracts written from this year use grant timing;
                                      # earlier ones keep close timing (default: the project year)
```

The `since` year exists for the transition: a contract written under the
old convention and closed after the switch would otherwise be taxed
nowhere (its premium was open at the prior year end; under the new rule
the close is only a loss). The comparison uses the row's sort date
(settlement for Canada), the same basis as the year filter.

**Engine.** The pool mechanics are unchanged — a written option still
retains its proceeds in `total_cost`, so inventory, harvest, list and
the holdings export keep the economic book cost. Grant timing is an
*emission* change:

- A pre-scan over the sorted rows walks each option symbol's short side
  FIFO by write order and marks, per sell-to-open row, how many of its
  units end up assigned (stock-settled ASSIGN). Pools are symbol-global
  (s.47), so FIFO runs across accounts, like the pool itself.
- Sell-to-open of a grant-timing row emits a **grant record** on the
  write date for its non-assigned units: `gain = +premium` (net of
  commission, so a commission larger than the premium is a small loss
  at grant), `cost = -premium`, `proceeds = 0`, `direction = SHORT`,
  `days_held = 0`, note `WRITE (s.49(1))`. The pool records the units
  and per-unit premium in a `grants` list.
- A close consumes grant units FIFO after any close-timing units. The
  closing record is today's pool gain **minus the premium already
  recognised** for the grant units consumed: a buy-back becomes a loss
  of the amount paid; an expiry becomes zero (suppressed when the whole
  close is grant units); a stock-settled assignment stages the full
  premium into the share leg exactly as today — its grant record was
  never emitted, so the two years already sit in the s.49(4) state.
- Full-history totals are identical under both settings (a fuzzer
  asserts this); only the year attribution moves.

**Amendment signal.** Because the engine computes from full history, a
grant year that was filed before an assignment happened shows the
premium as drift in `check-filed`. `taxjson option-boundary` says so in
words: which contract, which filed year, which amount, T1-ADJ or not.

## 2. Negative ACB is a deemed gain (s.40(3))

A return of capital that drives a held pool's ACB below zero is a
capital gain in the year of the distribution, and the ACB resets to
nil. The engine only warned. It now emits a `qty = 0` gain record dated
the ADJUST's settlement date for the excess and sets the pool to zero;
the note names s.40(3). Same lifetime total as before; the right year.

## 3. Short-sale losses and the superficial-loss rule (s.54)

The Canada wash pass accepted a new sell-to-open as the "replacement"
for a loss on covering a short and denied the loss if the position was
still short at day 30 — §1091(e) logic. Under s.54 the trigger must be
an *acquisition* of identical property that is still *owned* at the end
of the window; writing an option or selling short acquires nothing. The
Canada pass now applies the LONG criteria to every loss: triggers are
opening long acquisitions, the held test is a positive balance. (A
long re-purchase within the window after a cover loss still denies.)
Short-sale character (IT-479R para 18: income unless s.39(4)) is a
filing position the tool does not take; it is documented, and short
records carry `direction = SHORT` so they can be moved.

## 4. `taxjson option-boundary`

Lists every written option whose write and close straddle a tax-year
boundary (or that is still open at the project year's end) in the
taxable accounts, with: symbol, account, write date(s) and units,
premium, close date and kind, the timing that applies, where each
amount lands, and an instruction — "premium recognised in YYYY; no
amendment", "assigned in YYYY after YYYY-1 was filed: T1-ADJ YYYY-1 to
remove $X (s.49(4))", or, under close timing, "the Act puts $X in YYYY;
enable grant timing with since = YYYY to correct". A `filed/<year>.json`
lock is what makes "was filed" a fact rather than a guess.

## Impact list (from the study)

Numbers move: `sum`/`estimate` OPTION column and HOLD DAYS; `ccd-sum`
(two records per cycle; footer wording; direction explicit); `winners`
counts; `gains` native view (raw pass gets the flags); Schedule 3
export (grant row: proceeds = premium, ACB 0; buy-back row: ACB =
amount paid); `carryover` year netting; `close-year`/`check-filed`
(flags threaded; the snapshot records the timing it was filed under);
`audit` (verb WRITE; flags threaded); `reconcile-slips` (a write year
gains a disposition; brokers' T5008s may not carry it); `wash-sales`/
`explain` (flags threaded). Unchanged: positions, harvest/list/export
(economic cost kept), sanity, t1135, radar/sell-check/buy-check
(replay base rows), leaps views (LONG only), US engine, parsers.
