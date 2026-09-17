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

## Running tests

The package uses a `src/` layout, so tests import it **as installed** —
run `pip install -e .` (from Setup above) first, then:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

Or use the wrapper:

```bash
./run_tests.sh
```

### The full gate: `scripts/ci.sh`

Run this before every push. It is the authoritative CI — GitHub
Actions on this private repo is billing-gated, so the workflow file
(`.github/workflows/tests.yml`, Python 3.9–3.13 on Linux) is a mirror of
these stages rather than the source of truth:

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

## Adding a country rule set

Country-specific cost-basis and wash-sale logic lives in `src/taxjson/lib/core.py`. The Canadian ACB and US FIFO paths are the templates; add a new branch keyed off the `--country` CLI flag and write tests that exercise the boundary conditions (year-end carryover, multi-account aggregation, settlement-date edge cases).

## Pull request checklist

- [ ] Tests pass locally (`./run_tests.sh`)
- [ ] New behavior has a test that would have failed before the change
- [ ] No unrelated drift (no formatting churn, no rename cascades)
- [ ] PR description explains *why*, not just *what*

## License

By contributing, you agree your contributions will be licensed under the MIT License.
