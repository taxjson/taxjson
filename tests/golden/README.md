# Golden ledgers

Whole synthetic books and what they produce, recorded so a refactor can prove
it changed nothing. `tests/test_golden_ledgers.py` runs them in the suite;
`tests/_golden.py` holds the runner and says exactly what is compared: all of a
run's output (the reports, the exports, the answers in `work/`, stdout and
stderr) except the stages' intermediate files in `work/`, with the clock masked.

A project book can also list `views` in its `book.json`: `tjs` commands run in
order after the run (`sum`, `estimate --json`, `close-year --yes`, ...), or a
taxjson-* tool's module (`{"tool": "taxjson_sum_gains", "argv": ...}`: a
document no command prints), their stdout, stderr and exit codes and the files
they write recorded under `expected/_views/`. Every `--json` output has a view
on a book, or a test that pins it (`tests/test_json_contract.py`).
`tests/test_golden_views.py` compares those, and
`tests/test_golden_clock.py` runs every project book a thousand days later, adding
floats with Python 3.11's sum(), and expects the same answer (`tests/_clockshift`,
`tests/_oldsum`): the books are recorded on 3.12, whose sum() compensates. A view
that reads the clock is masked in `_STAMPS`, or left out.

| Book | What it pins |
|---|---|
| `demo` | The `tjs init --demo` project: eight accounts, the blended pass; views: the summaries, close-year, then 2025's run and handoff |
| `style-canada`, `style-usa` | The output-style projects, elections saved; views: the summaries (style-canada with slips, BC beside the flag's ON, and a prior-year instalment basis owing interest), close-year; style-usa's Kraken ledgers export, its fees taken in coins sold (USDC at par, ETH at the feeusd, and a dust reward's), a Coinbase USDC reward bought as property, and a Questrade call's expiry posted the next business day (its ASSUMED contract size noted in each wash .sum) |
| `options` | Every way an option opens and closes, across a year end; Questrade and RBC Direct expiries posted the next business day (one in the next year's export, its 0DTE trade's settle clamped to it) and an RBC warrant's |
| `crypto` | Two exchanges, a swap, staking, superficial losses, a gift; views: crypto-sends, staking and staking-sum (`--json`) |
| `evidence` | The money packed into a row's strings (model/evidence's codecs read them back to themselves, tests/test_model_evidence_books.py): IB closed lots and a commission refund, a future in two accounts, a cash in lieu paid late, an OPENING's cut legs; a return of capital and a payment in lieu (roc-sum, dil-sum); views: events, fx-cash (both ledgers), find-missing-history, audit |
| `fuzz-conservation`, `fuzz-transfer`, `fuzz-straddle` | One frozen fuzzer book each, through both engines |

Each `book.json` says where the book came from. The inputs are copies, so a
change to the demo or to a fuzzer does not move these answers.

To record a change that means to move a figure or a message:

    venv/bin/python3 tests/_golden.py --update [book ...]

Read the diff of `expected/` before committing it: that diff is the change.

If the PII gate says a recorded number matches a figure from your own books,
change a price in that book's inputs and record it again. The gate has no
exception for figures, and the inputs here are made up anyway.

The push gate also reads what follows a `#` in any added line as a comment, and
a comment's money amount with thousands separators stays under 1,000. A trace's
summary rows start with `#`, so a book's gains stay under that: lower a price and
record it again. `TestWhatABookHolds` fails first, in the suite: `scripts/ci.sh`
scans the tree, and only the push gate reads the diff.
