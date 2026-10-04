# Contributing to taxjson

Thanks for your interest. This project computes numbers that real people put on real tax returns, so correctness is the top priority. Contributions of all sizes are welcome — bug reports, parser support for new brokerages, rule coverage for additional countries, and documentation fixes.

## Ground rules

- **Every fix lands with a regression test.** Pipeline correctness is preserved primarily through the test suite. A change without a test that would have failed before it is unlikely to be merged.
- **Don't change pipeline output silently.** If your change alters the JSON shape, sums, or counts emitted by any `taxjson-*` tool, call it out in the PR description and update affected tests / baselines.
- **No mocks for tax math.** Tests that hit the cost-basis solver, wash-sale matcher, or corp-action rewriter should exercise real code paths against real fixtures.

## Setup

```bash
git clone https://github.com/taxjson/taxjson.git
cd taxjson
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

## Help wanted: broker exports

The most valuable contribution is a **redacted real export** from a broker
the parsers don't cover yet (Wealthsimple, TD Direct, BMO InvestorLine,
CIBC Investor's Edge, Scotia iTRADE, National Bank Direct, Qtrade,
Desjardins…) or a US account for the experimental US engine. Run

```bash
taxjson redact ~/Downloads/activity.csv        # writes activity.redacted.csv
```

It replaces every account number with a same-length placeholder (so the
file still parses), removes name/alias/address rows and e-mail addresses,
and prints a report. Read the free-text description column once for
names, then attach the `.redacted.csv` to an issue or pull request.
Quantities, prices, dates and symbols are kept — that is what a parser
needs. Never attach an un-redacted statement.

## Before anything is pushed: personal data

This is a public repository, so a push is publication. `scripts/check-pii.sh`
scans for broker account-id shapes, home paths, unlisted e-mail addresses,
credential-looking strings, and — for the maintainer — a **private denylist**
at `~/.config/taxjson/pii-denylist` (one regex per line: your real account
numbers, name, addresses; it lives outside every repository, so the strings
it guards are never themselves committed). It runs in every `scripts/ci.sh`
mode and as the `pre-push` hook that `scripts/dev-setup.sh` installs, which
scans only what a push would add — the diff lines (the net diff and the
added lines of every pushed commit: a value added in one commit and removed
in the next is still published), the text inside binary
files (PDF/Office metadata, spreadsheet cells), branch and tag names,
commit and tag messages, and author/committer/tagger identities (against
the denylist and the e-mail allowlist) — and refuses on any hit (your own
configured identity only warns). `scripts/release.sh` runs the same hook
itself before it pushes. Account-id shapes count in folder names as well
as file names, and an 8-9 digit value under an `Account` / `Account #`
CSV column counts as an account number; other broker id layouts are
caught only by your private denylist, so fill it in.
Mark a genuine false positive with a `# pii-ok` (or `pii-ok:`) comment on that
line — the bare word does not count; bypass knowingly with
`git push --no-verify`. Fixtures must be synthetic: fake account ids
(`U1234567`, `99900001`), made-up ISINs, no real statements.

**Never put figures, ids or names from a real person's books into code,
tests, docs, the CHANGELOG or commit messages** — yours or anyone else's.
Use synthetic inputs: made-up amounts, quantities, dates, tickers and
ids (a test builds its expected numbers from a synthetic input it
states). CHANGELOG entries describe a change in words; a worked example
with numbers is allowed only when it is computed from a synthetic input
stated next to it. Commit and tag messages are public history and cannot
be scrubbed: no real amounts or totals, no symbol + quantity pairs or
trade dates from real books, no account ids — write "the 2025 total is
unchanged", not the number. The `pre-push` hook enforces part of this: a commit or tag message
line with a money-like amount (thousands separators and cents, such as
`1,234,567.89`) is refused (`scripts/check-pii.sh --message`), and so is <!-- pii-ok: the synthetic example -->
one added to a CHANGELOG or markdown doc line or a code comment in a pushed
commit (`--diff`). A synthetic number is let through by the word `pii-ok`
on its line (in a doc, `<!-- pii-ok -->`).

The maintainer also keeps a **private figure list**: the distinctive
money figures (amounts with cents, five or more digits, cents other than
.00/.25/.50/.75) of their own projects' outputs — `reports/`,
`work/*.sum`, `*.toml`, `*.tt` — and the distinctive values of the raw
broker exports under `inputs/`: amounts with cents from six digits up,
numbers with three or more decimals and six or more significant digits
(prices, rates, fractional and coin quantities), broker reference codes
(six or more letters and digits with at least two digits — internal
security and option codes, option symbols with their root, order
references — or a number of seven or more digits that is not a date),
and clock times with seconds next to their date. Short, round and public
values are never listed (a word ending in a 2-digit number, a month-name
date, an option series without its root, an ISIN or CUSIP, a time on
the minute), so a synthetic example rarely collides; when one does, use
another synthetic value. All are stored only as salted SHA-256 hashes
(mode `0600`, no plain figure on disk) at
`~/.config/taxjson/pii-amounts` (override with `TAXJSON_PII_AMOUNTS`).
Build or refresh it after a run with

```bash
scripts/check-pii.sh --collect-amounts path/to/project-2025 path/to/project-2026
```

(it merges into the existing list). Every check-pii mode — the tree scan
in `ci.sh`, the pre-push diff and per-commit scan, commit and tag
messages, ref names — then refuses a line holding one of those figures,
naming the file and line ("matches a figure from your own books") but
never the figure. There is no `pii-ok` escape: if a synthetic number
collides with one, pick another number. Contributors have no list, and a
missing list at the default path is skipped silently; a list named by
`TAXJSON_PII_AMOUNTS` that does not exist, or one that cannot be read,
fails the scan. The hashes keep the figures out of plain sight on disk;
they are not a defence against someone who already has the file, so keep
it private like the denylist.

## Running tests

The package uses a `src/` layout, so tests import it **as installed** —
run `pip install -e .` (from Setup above) first, then:

```bash
python -m unittest discover -s tests -p "test_*.py" </dev/null
```

Or use the wrapper:

```bash
./run_tests.sh
```

The broker-fetch plugin (`packages/taxjson-fetch`) has its own tests;
they run from a checkout without installing the plugin (its
`tests/_support.py` registers the entry point for the run), or after
`pip install -e packages/taxjson-fetch`:

```bash
PYTHONPATH=packages/taxjson-fetch/src python -m unittest discover -s packages/taxjson-fetch/tests -p "test_*.py" </dev/null
```

### The full gate: `scripts/ci.sh`

Run this before every push. It is the authoritative CI; the workflow
file (`.github/workflows/tests.yml`, Python 3.9–3.13 on Linux) mirrors
its stages for pull requests on the public repository — lint,
consistency, tax-rules, the PII scan (generic patterns only: a hosted
runner has no private denylist) and the suite. A missing `ruff` fails
the lint stage; `pip install -e ".[dev]"` (or `scripts/dev-setup.sh`)
installs it:

```bash
scripts/ci.sh            # lint (ruff, critical tier) + suite + fuzzers at CI depth  (~2 min)
scripts/ci.sh --nightly  # fuzzers at 5000/3000/1600 books                            (~5 min)
scripts/ci.sh --quick    # lint + suite only                                          (~40 s)
```

Every run appends one line to `.ci/history.log` (gitignored) with the
commit, mode, result and wall time, so "when was this last green?" has
an answer.

### Property fuzzers

Three seeded fuzzers assert LAWS of the domain over generated books —
they are what keeps finding real engine bugs after the example tests
stopped (each of the last three engine defects was a fuzzer catch):

| Test module | Property | Depth env var (default) |
|---|---|---|
| `test_engine_invariants` | conservation, no phantom denials, order invariance, sign, solver health — both engines | `TAXJSON_FUZZ_BOOKS` (200) |
| `test_transfer_fuzz` | custody-transfer netting/restatement/attestation: no crashes, conservation, determinism | `TAXJSON_TRANSFER_FUZZ_BOOKS` (150) |
| `test_settle_straddle_fuzz` | settle-lagged trades vs splits: refusal semantics, conservation, lag-no-op and straddle differentials | `TAXJSON_STRADDLE_FUZZ_BOOKS` (200) |

A failure prints its seed and the generated book; rebuild the book
with the module's generator in a scratch script and shrink from there.

### Mutation testing

`scripts/mutation_audit.py --yes` mutates the wash-relevant regions of
`core.py`/`pipeline.py` one operator at a time and reports survivors —
mutants no test kills. Triage survivors with `scripts/mutation_triage.py`
(equivalent mutants vs genuine test gaps). **It rewrites engine files in
place**: run it alone on a clean tree, never alongside edits, and if it
is interrupted restore with `git checkout -- src/taxjson/lib/`.
`--list-targets` shows the regions without running anything.

## Adding a brokerage parser

1. Subclass `BaseBrokerage` in `src/taxjson/lib/brokerages/your_broker.py` and
   implement `parse_file(path) -> List[Dict[str, Any]]`. Reuse the `base.py`
   helpers (option symbol formatting, currency suffix, fee back-compute,
   dividend qty/rate extraction, `tx_roc_adjust`) instead of reinventing them,
   and follow the schema in `src/taxjson/lib/brokerages/schema.py` — it is
   enforced: `taxjson-brokerage` validates every parse against it.
2. Count what you drop: rows the parser can't classify go through
   `self.count_skip('<category>')` (see the Questrade/RBC/Coinbase dispatch
   loops); `taxjson-brokerage --lint` reconciles rows in vs rows out.
3. Register the id(s) in `src/taxjson/bin/taxjson_brokerage.py`
   (`register_brokerage(...)`) and add a detection branch in
   `src/taxjson/bin/taxjson_detect_brokerage.py`.
4. Add a SYNTHETIC fixture (never real account data) at
   `tests/fixtures/your_broker/sample.csv`, register a three-line subclass in
   `tests/test_parser_conformance.py`, and mint the golden with
   `UPDATE_GOLDEN=1 python -m unittest tests/test_parser_conformance.py`.
   The conformance kit (schema, golden, determinism, skip accounting) plus a
   few behavior-specific unit tests is the review bar.
5. `taxjson-generate-parser` can draft step 1 from a CSV sample — its prompt
   is generated from the same schema table, but the draft still goes through
   steps 2–4 like hand-written code.

## Adding a broker fetcher (`taxjson fetch`)

Broker API clients never go in the core: the core holds no broker
client and reads no broker credential, and its only network egress is
FX rates and crypto prices (SECURITY.md). `taxjson fetch` is a thin
dispatcher over plugins, so a new broker's downloader is its own
package:

1. Write a class with `brokerages` (the `brokerage = "..."` values it
   serves under `[accounts.<name>]`), `description`, and
   `fetch(request) -> {account: {...}}` that writes each account's
   activity into `request.root / "inputs" / <account>` in a format an
   existing parser reads. Optional: `add_arguments(parser)`,
   `account_keys`, `setup_hint`. The contract is documented in
   `src/taxjson/lib/fetchers.py` (and README, "Writing a fetcher for
   another broker").
2. Register it in your package's `pyproject.toml`:
   `[project.entry-points."taxjson.fetchers"]` → `mybroker =
   "mybroker_fetch.plugin:Fetcher"`, and depend on `taxjson`.
3. Test it offline: inject the HTTP layer (see
   `packages/taxjson-fetch/src/taxjson_fetch/api.py`'s `http_get`) and
   round-trip the written file through the core parser. Never commit a
   real token or account number.

`packages/taxjson-fetch` (Questrade REST API, IBKR Flex) is the worked
example; a fetcher kept in this repository lives under `packages/` and
`scripts/ci.sh` runs its tests.

## Adding a country rule set

Country-specific cost-basis and wash-sale logic lives in `src/taxjson/lib/core.py`. The Canadian ACB and US FIFO paths are the templates; add a new branch keyed off the `--country` CLI flag and write tests that exercise the boundary conditions (year-end carryover, multi-account aggregation, settlement-date edge cases).

## Canada and US law never mix

- **One country resolver.** Read a project's or a flag's country only
  through `src/taxjson/lib/country.py` (`canonical_country`,
  `settings_country`, `country_arg`, `resolve_tax_date`,
  `home_currency`). A missing or unknown country is an error there, never
  a silent Canada.
- **Ownership tables.** A setting, config table, engine flag or command
  that belongs to one country goes in `SETTING_COUNTRY`,
  `CONFIG_COUNTRY`, `FLAG_COUNTRY` or `COMMAND_COUNTRY` (with its
  `*_WHY` reason). The config readers, the engine CLIs and `taxjson`'s
  dispatch refuse the other country's entries from those tables; tax-logic
  states them.
- **Country gates live in the engine, command or config layer.** Parsers
  emit neutral facts; they never decide one country's tax treatment.

## tax-logic is the spec

Every statement `taxjson tax-logic` prints is a `Rule` with a stable id in
`src/taxjson/lib/tax_logic.py` (`CA-...` in the Canada section, `US-...` in
the US section; `taxjson tax-logic --ids` shows them). A change in tax
logic changes or adds a Rule in the right country's section, and the test
that pins it cites the id:

```python
from tax_rules import rule, rule_absent
from tax_rules.dual import gains_both, tx

@rule("CA-SL-02")                         # Canada applies it ...
@rule_absent("CA-SL-02", country="usa")   # ... the same book under the US does not
@rule("US-WASH-06")
def test_still_held_at_day_30(self):
    r = gains_both(book)                  # one synthetic book, both countries
    ...
```

While a test runs, its markers restrict which country's engine may run (an
`AssertionError` otherwise, also in a `taxjson` subprocess). Behaviour that
belongs to one country needs a dual-country test like the one above.
`scripts/check_tax_rules.py` (a stage of `scripts/ci.sh`) fails on an unknown
or retired id, a one-country test that names the other country's engine, a
rule without a test beyond the shrink-only baseline
(`tests/tax_rules/baseline-unpinned.txt`), a partition rule
(`PARTITION_RULES`) without its `@rule_absent` pair beyond
`baseline-unpaired.txt`, and a `[settings]` key no rule names. When a rule
gains a test, delete its baseline line; a new rule can never be baselined.
Retire an id in `tests/tax_rules/retired.txt`; never reuse one.

## Pull request checklist

- [ ] Tests pass locally (`./run_tests.sh`)
- [ ] New behavior has a test that would have failed before the change
- [ ] No unrelated drift (no formatting churn, no rename cascades)
- [ ] PR description explains *why*, not just *what*

## License

By contributing, you agree your contributions will be licensed under the MIT License.
