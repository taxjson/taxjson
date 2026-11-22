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
scripts/dev-setup.sh     # venv/, editable install with the [fx,dev] extras, the taxjson-fetch plugin, the pre-push hook
source setup.sh          # activate it (each new shell)
```

`scripts/dev-setup.sh` is idempotent (re-run it after a pull). It installs
the dev lock (`requirements/dev.txt`, hash-pinned: the ruff CI lints with),
the core with `pip install -e ".[fx,dev]"`, then the broker-fetch plugin
with `pip install --no-deps -e packages/taxjson-fetch` (`--no-deps`: its
only dependency is the core you just installed — taxjson is not published
on PyPI, and a `taxjson` package there is not ours), removes the
`*.egg-info` folders those builds leave beside the sources and checks that
the venv's versions of both match the code (`scripts/editable_check.py`;
an install keeps the version it was made at, so re-run setup after a
version bump), installs the pre-push hook (tag guard and personal-data
scan, below) and scaffolds your private denylist. `scripts/dev-setup.sh --hook-only` (re)installs just the hook.
End users install with the one-line installer instead (README, "Install").

## Help wanted: broker exports

The most valuable contribution is the **shape of a real export** from a
broker the parsers don't cover yet (Wealthsimple, TD Direct, BMO
InvestorLine, CIBC Investor's Edge, Scotia iTRADE, National Bank Direct,
Qtrade, Desjardins…) or from a US account for the experimental US engine.
How to share it — the same rule as the bug-report template, README and
SECURITY.md:

1. **Never attach a raw export** or anything copied from one.
2. **First choice, a synthetic file:** the header and a few rows of each
   kind (buy, sell, dividend, fee, transfer, corporate action…), with the
   broker's exact columns, action words and wording pattern, and made-up
   symbols, ids and amounts. Its amounts are made up, so they are fine.
3. **Otherwise a redacted copy, after review.** `tjs redact` copies an
   export and strips what it recognises:

   ```bash
   tjs redact ~/Downloads/activity.csv        # writes activity.redacted.csv
   tjs redact                                 # in a project: inputs/ -> inputs_redact/
   ```

   It replaces every account number with a same-length placeholder (so
   the file still parses), removes name/alias/address rows and e-mail
   addresses, and prints a report. Run with no file in a project, it
   copies the whole `inputs/` folder to `inputs_redact/` and redacts the
   copy (file names that hold an account number included; the old → new
   names are printed, never written into the copy); `inputs/` is left as
   it was, and binary files (`.xlsx`, `.pdf`) are not copied. It is
   pattern-based and keeps quantities, prices, amounts, dates and symbols
   (what a parser needs): read all of it — the free-text description
   column especially — and attach it only after that review, and only if
   you are comfortable sharing those real amounts.

## Before anything is pushed: personal data

This is a public repository, so a push is publication. `scripts/check-pii.sh`
scans for broker account-id shapes, home paths, unlisted e-mail addresses,
credential-looking strings (PEM private keys, GitHub / Anthropic / Slack /
AWS key formats, a high-entropy value after a key / secret / token name), and — for the maintainer — a **private denylist**
at `~/.config/taxjson/pii-denylist` (one regex per line: your real account
numbers, name, addresses; it lives outside every repository, so the strings
it guards are never themselves committed). It runs in every `scripts/ci.sh`
mode and as the `pre-push` hook that `scripts/dev-setup.sh` installs (into
the clone's own hooks folder, also when a global `core.hooksPath` chains
to it; `--hook-only` installs just the hook), which
scans only what a push would add — the diff lines (the net diff and the
added lines of every pushed commit: a value added in one commit and removed
in the next is still published), the text inside binary
files (PDF/Office metadata and compressed PDF text, spreadsheet cells) —
every revision of a binary file a pushed commit adds, not only the one at
the tip — branch and tag names,
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
A pull request (and a push to `main`) gets the same check in CI with the
generic patterns: every commit it adds (patch, message, author and
committer identities), so
commit with your GitHub `…@users.noreply.github.com` address.

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
in `ci.sh`, the pre-push diff and per-commit scan (merge commits
included: the lines a merge adds against every parent), commit and tag
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
run `scripts/dev-setup.sh` (Setup above) first, then:

```bash
python scripts/run_tests_parallel.py          # the suite on min(CPUs, 16) processes
python scripts/run_tests_parallel.py test_x test_y   # some modules, in parallel
python -m unittest discover -s tests -p "test_*.py" </dev/null   # serial
```

`scripts/run_tests_parallel.py` (standard library only) runs each test
module in a process of its own — its own `TMPDIR` and its own synthetic
HOME — `--jobs N` at a time, longest first by the times the previous
run recorded in `.ci/test-durations.json` (gitignored; the file size
stands in on a fresh clone). It prints a line per module that fails
(`-v`: every module), each failure's full output, the total and the
slowest 15 modules, and it fails unless every module passed AND the
tests run add up to the serial discovery's count. A module slow enough
to hold up the whole run is split into a process per test class. It
never retries: a test that fails only in parallel shares something it
must not — a fixed temp path, a port, a file in the checkout — and is a
bug to fix. `--serial` runs the plain serial discovery.

Helpers, fixture text and rows that more than one test module uses live
in `tests/_support/` (`from _support.ib_statement_rows import _trade`):
a test module never imports another test module. `CROSS_IMPORTS_MAX` in
`tests/test_test_layout.py` counts the imports between test modules still
left, and only goes down; `scripts/extract_support.py TABLE` moves a
module's shared names there, and its `--check` proves the move (its
docstring says how). A support module's name starts with a broker, a
country or an area (`SUPPORT_PREFIXES`, the same file): a reader finds a
helper by its subject; the B1z renames are in
`scripts/support-renames.tsv`.

Or use the wrapper (serial, verbose):

```bash
./run_tests.sh
```

The broker-fetch plugin (`packages/taxjson-fetch`) has its own tests;
they run from a checkout without installing the plugin (its
`tests/_bootstrap.py` registers the entry point for the run), or after
`pip install --no-deps -e packages/taxjson-fetch` (after the core: `--no-deps`
keeps pip from fetching a `taxjson` from PyPI to meet its dependency):

```bash
PYTHONPATH=packages/taxjson-fetch/src python -m unittest discover -s packages/taxjson-fetch/tests -p "test_*.py" </dev/null
```

Run taxjson on a project with the `taxjson` (`tjs`) console script, not
`python -m taxjson.bin.taxjson_run` from inside the project folder:
`python -m` puts the current directory first on `sys.path`, so a
`json.py` or `csv.py` lying in the project would be imported and run
(`python -P -m ...` is safe). Code that starts a child
Python process uses `taxjson.lib.dispatch.python_module_argv`, never
`[sys.executable, "-m", ...]`.

A change to how a report prints that means to move no byte (PLAN step 15,
one report renderer) proves it with `scripts/style/byte_proof.py`: in a
clone at the commit's parent and in one at the commit, each holding this
commit's copy of the tool's files (`TOOL_FILES`: the script,
`tests/_clockshift/sitecustomize.py`, `tests/_shimchain.py`,
`tests/_golden.py` and `tests/_hermetic/__init__.py`), one recording at a
time and both with one TMPDIR (a temp folder's path is labelled, but its
length still moves a wrap or an archive's size; `--compare` refuses two
of other lengths), `python3 scripts/style/byte_proof.py --record DIR
--jobs 3`, at the parent with `--reach-head COMMIT` and at the commit with
`--reach-base PARENT` (each traces the files of src/taxjson the two trees
differ in, and watches the lines its side of the diff changes), then
`--compare BASE_DIR HEAD_DIR --trees PARENT COMMIT`, which must say
`equal` (exit 0; exit 3, equal with a warning, means both recordings are
of one tree). The reach gate refuses it (exit 4, naming each `file:line`,
`(BASE)` for a line of the parent, and what it needs) unless each line
the commit changes was reached as its rules ask (below), or is listed in
`scripts/style/byte_proof_unreachable.txt` with a reason: `[BASE
]src/taxjson/PATH:LINE :: TEXT :: REASON`, an entry that excuses that one
line while it holds TEXT. As a control, record the parent twice more,
once with a comment appended to a module of src/taxjson, each traced
against the other as above: `--compare` must say `equal` too. The
commands, by default: every golden book's `tjs run`, its views (../2025's
too), the tool's `EXTRA_COMMANDS` (a few stand-alone `taxjson-*` tools
among them; each prompt answered on the terminal, `# stdin: y`, and
refused off it; the messages of a project edited first, `# edit: append
PATH TEXT`, and built first when they need books, `# before: run
--no-input`, an edit after it made after it), its
`OPTION_LINES` and every registry command's default form, `--details` and
`--json` (these two at the first instant only); every option of the
registry is passed by one of them or listed in `REVIEWED_OPTIONS` with its
reason, and `--list-commands` names what they reach. `--argv FILE` adds
lines (`BOOK ARGV...`, then `# exit N`, `# stdin: TEXT`, `# edit: OP PATH
[TEXT]`, `# before: ARGV`), never in their stead. Each command runs on a
fresh copy at two instants (`INSTANTS`, 21:30 in November and in March;
the children's clock stands still, the copies' files are dated a day and a
half earlier): at TAXJSON_WIDTH 0 and 40 and unset, stdout and stderr
apart and merged; on a terminal (a pty of 100 columns: colour, the
terminal's width, a prompt reading the line's stdin, then EOF); with
stdout piped and stderr and stdin on the terminal, and the reverse; with
stdout's reader gone (`| true`) when it wrote to stdout; with
PYTHONIOENCODING=ascii when it wrote a byte past ASCII; and, when its
bytes move with the width (at 40 not as at 0, or on the terminal not as
at 0), at 120, 160, 30 and a TAXJSON_WIDTH that is not a number, on a
terminal of 30 and of 200 columns (under out's floor and over its cap)
and with COLUMNS=72 on the terminal and, with NO_COLOR, on pipes; a
command that colours (`COLOURS`, or an escape on the terminal) once more
with NO_COLOR set. Its environment is the tool's own (no NO_COLOR, LANG
fixed, a HOME with synthetic rates), and every child runs in a network
namespace of its own (`unshare --user --map-current-user --net`) under a
shim that refuses each socket connect and name lookup and says so: where
no namespace can be made the tool refuses to record. Each child leads a
process group of its own, killed when it exits, when it is refused (900
seconds, or a test's timeout) and when the tool dies (`setpriv --pdeathsig
KILL`): nothing a command starts outlives it. Every file, folder and mode
a command changes in the copy, the folder above it and HOME is recorded,
the files as bytes; only the folders, a build record's commit and the
package fingerprint are labelled. A command that exits 2 (a usage error)
is refused unless its line says so (`ARGV # exit 2`; such a line passes
no option); a line naming a
command that pushes, installs, changes a release channel, writes outside
the copy or reaches the network (`DENIED`: `fetch`, `channels`, `deploy`,
`promote`; `DENIED_FORMS`: `tips --online`, `parser-check
--update-golden`), a stand-alone tool but those five, a glued `-C`, or an
argument, edit or appended text that names a path out of the copy
(absolute, `~`, `..`, through a link) is refused before anything runs. `--compare` checks every
file against the recording's manifest and refuses recordings of other
instants, passes, environments, isolation, Pythons, packages, tools,
commands or checkouts (a clone's against an archive's). One recording of
the defaults (1365 command lines and nine runs, 107,933 files) takes 50 to
80 minutes at `--jobs 2` or 3, traced or not, by the machine's load (77
minutes each, traced, at `--jobs 2` under a load of 12 to 15); a commit
records two, its parent and itself, and `--compare` with the reach gate
takes about a minute.

What the proof shows, and what it does not. Equal bytes show that the
parent and the commit print and write the same on the inputs recorded:
the commands above, on the golden books, at the instants, widths and
terminals of the passes. The reach gate (`reach_gate`) bounds how much of
the change those inputs cover. On each side, each line the diff adds,
changes, deletes or replaces ran: in every code object on it (a lambda
on it called, not just the line around it); a line with no instruction of
its own, through the part of its statement on it and each conditional
around that part; a clause line (`else:`, `finally:`), through the first
statement of its body. Every branch out of such a line (if, elif, while,
a for's end, a conditional expression, each operand of an and/or, a
comprehension's condition, a match case, an except's match) went both
ways; a line in a
loop's or a comprehension's body ran in two iterations of one entry of the
loop (a closure that reads the loop's variable later differs only then);
a default on it was used; each function whose body changed was called.
Each changed text (a string's pieces between its format fields, of four
characters or more) shows in the commit's recorded output; a changed file
that is not Python was opened, and its changed lines show. Each statement
keeps the tries, finallys and withs around it (a statement moved out of
its try, or a call moved into one, is refused, whatever ran). A byte that changes no code (a
comment, a blank, a line's end, a coding cookie) needs its module
imported. What it does not show: that every input prints the same. A
change whose bytes differ only on a value no recorded command makes (a
thousands separator no amount of four digits reaches, a date past the
instants, a branch taken both ways but never with the value that differs)
escapes it; the branch, loop and text rules are what bound that, and such
a change needs a command line that makes the value (`--argv`) or a
reviewer who checks it.

What an equal comparison is, after the fifth soundness review (Fable,
2026-11-20; backlog TJ-4820 to TJ-4826): BASE and HEAD wrote the same
bytes for the commands recorded, on the golden books, at the instants,
widths and terminals of the passes, and nothing past them. The reach
gate does not make it a proof: it asks that each changed line ran, not
that every input prints the same, and the review passed changed bytes
through it in five classes: a block moved into a new function with its
text kept, which the diff aligns as unchanged (a `return` that now
leaves only the helper); a report held back and printed at its end, so a
crash or a stderr line part-way shows other bytes; a changed binding
read only on lines no command ran; a difference only some data shows (no
row, one item, an empty last column, a longer line, a text of under four
characters, an exception no input raises); and an UNREACHABLE entry,
which waives every rule of its line. Its patching stopped there. The
byte proof is a regression check (main against step 15's combined tip),
not a proof; each step 15 commit is proved by structure, by AST checks
kept with the step's records: a move by inlining each new function back
at its call (BASE's AST required), a conversion by rewriting each op of
a live exact Doc back to the print it is (BASE's AST required).

### The full gate: `scripts/ci.sh`

Run this before every push. It is the full local gate (fuzzers, the
private lists); the workflow file (`.github/workflows/tests.yml`, Python
3.12 and 3.13 on Linux) runs its stages on every pull request and push to
`main` — lint, the architecture ratchet, consistency, tax-rules, the PII scan (generic patterns
only: a hosted runner has no private denylist), gitleaks and the suite —
and its checks must pass before a change lands and before a release is
tagged (docs/releasing.md). A missing `ruff` fails
the lint stage, and one of another version than the dev lock's
(`requirements/dev.txt`, which CI installs) gets a note; a missing `mypy`
fails the types stage (strict on the packages `pyproject.toml`'s
`[tool.mypy]` names: model, rules, characterize and the engine; a function
there is annotated in full, but in the engine modules its overrides table
still silences: that list only shrinks, as step 10 types them). `scripts/dev-setup.sh` installs the lock's:

```bash
scripts/ci.sh            # every stage: lint (ruff: critical tier, unused imports), types (mypy), consistency, tax-rules, pii, architecture, suite, no-extras, fetch-plugin, fuzzers at CI depth
scripts/ci.sh --nightly  # fuzzers at 5000/3000/1600 books
scripts/ci.sh --quick    # every stage but the fuzzers
scripts/ci.sh --serial   # ...with the suite in one process (the old way; a fallback)
```

The suite stage runs `scripts/run_tests_parallel.py` (`TAXJSON_TEST_JOBS=N`
picks the process count). Measured on a 20-core machine (2026-10, ~8,350
tests): serial 1,054 s; parallel 107 s on 16 processes (94 s on 20,
128 s on 12, 180 s on 8, 327 s on 4). No module dominates: the slowest
take 25-45 s, under the run's own length, so none is split. The whole
default gate: 129 s (suite 98 s, fuzzers 10 s, the other stages ~20 s),
was 18-20 minutes.

Every run appends one line to `.ci/history.log` (gitignored) with the
commit, mode, result and wall time, so "when was this last green?" has
an answer.

The suite runs in an empty HOME, offline, as on a GitHub runner: every
test process gets a synthetic HOME with made-up exchange rates and
`TAXJSON_OFFLINE=1` (`tests/_hermetic`), so a test never reads your rate
cache or the network. A test of an online path injects its fetcher, or
clears `TAXJSON_OFFLINE` itself. GitHub's matrix jobs install no optional
extras, and run the suite before they install the broker-fetch plugin
(taxjson-fetch): a test that needs an extra skips without it, a test of
`fetch` holds with no fetcher installed ("no fetcher is installed"), and
the gate's `no-extras` stage (`scripts/ci_no_extras.sh`, seconds) runs
the tests that touch an extra or the plugin with every extra and the
plugin hidden. Add a new extras test module to its `EXTRAS_TESTS`; a
module that runs `fetch`, reads `lib/fetchers.py` or names
`taxjson_fetch` is found by its text. `scripts/ci_no_extras.sh
venv/bin/python3 test_x` runs any module that way. The gate's whole
suite runs with the plugin hidden too (`scripts/hide_optional.py`); only
its `fetch-plugin` stage sees it.

The gate runs the suite on the dev venv's Python, 3.12, the floor
(`requires-python`), so what the floor refuses fails there; CI's matrix
also runs Python 3.13. The suite refuses a child Python started with a
`-X` option newer than 3.12, which 3.12 takes and ignores. For what 3.13
removed or changed, run the suite on 3.13 before landing a change that
might meet it: `python3.13 -m venv ../venv313 &&
../venv313/bin/pip install -e .`, then `PYTHONPATH=$PWD/src
../venv313/bin/python scripts/run_tests_parallel.py`.

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
the gains engines (the modules below `src/taxjson/engine/`, step 10 of the
engine refactor) and
`src/taxjson/lib/pipeline.py` one operator at a time and reports
survivors — mutants no test kills — each by its repo-relative path.
Triage survivors with `scripts/mutation_triage.py` (equivalent mutants vs
genuine test gaps). **It rewrites engine files in place**: run it alone
on a clean tree, never alongside edits, and if it is interrupted restore
with `git checkout -- src/taxjson/`.
`--list-targets` shows the regions and the parser targets without running anything.

`--parser ID --yes` mutates one parser instead (a registry id): a
sample of its sites stratified by function (`--sample N --seed S`, 150 and 0 unless
given; `--list-sites` prints them without running), against the test modules a line
trace of every test module finds running it, cut per mutant to those that ran the
mutated line (the golden books whole), less those its control (the target unparsed,
unmutated) fails, named in the report and the record. Each set runs its modules longest
first (by the trace) and stops at the first that fails, which the report names ("killed
by B: tests.x": the first in that order to fail; one after it may fail too, unrun). A
kill counts once that module fails again alone with the mutant and passes alone with the
target unmutated, so the module named kills the mutant by itself; one that does not (a
flaky module, or one that fails only after others) is set aside for that mutant, named
on its line and in the record (`set_aside_per_mutant`), and the set goes on with the
modules after it. A module that runs out is run alone unmutated: passing there, the
mutant ran it out (a timeout); failing or running out there too, it is set aside for
that mutant the same way. A mutant that passes is confirmed against the quick modules
its cut left out before it is called a survivor; a kill there is a cut miss
(`cut_misses` in the record), a bug of the trace, and a run with one runs its survivors
against the rest of the slow modules too. A survivor whose line a module left out of the
whole run (`set_aside`) reaches, or for which a module was set aside, is flagged
(`flagged`); a module set aside for another mutant ran for it, and flags nothing there.
The trace decides the work, never the verdict: a kill rests on the control and its
module alone, with and without the mutant, a survivor on all the quick modules, the
golden books and the slow modules the trace cut. A process of the trace that cannot tell
its test module, or ends before its last record, is counted in the report and the record
(`lost`, `unflushed`); a run with the first, or with the second in a module of the quick
or slow sets, is not recorded.

`--jobs N` runs N mutants at once, each in its own copy of the tree (in a temporary
folder, the venv linked; removed at the end). A copy has a git store of its own that
sees it as the tree's git sees the tree (its index, HEAD and refs, the objects read from
the tree's store, never written to), so a module that runs git on the tree
(`test_parser_check`, the release tests) runs in a copy as in the tree; a tree that is
no checkout gives copies without git. Git's location variables (`GIT_DIR`,
`GIT_INDEX_FILE` and the rest `git rev-parse --local-env-vars` names: a git hook sets
some) are dropped from every environment the harness gives git and the tests. The
control runs in the first copy, and what it sets aside is set aside in every copy. A run
is reproducible for one saved trace: the trace's seconds decide which modules are quick,
the order a set runs in and the slow modules' limits, and they are the machine's under
the jobs the trace ran at, so the report and the record say the trace's jobs and the
control's (`trace_jobs`, `control_jobs`), and the record names the saved trace by its
file's sha256 (`trace`). Given one saved trace, the report and the record are the same
for any N but for those: the report's lines follow the sample's order and carry no time
(stdout has the times). A copy whose test process is killed from outside (the OOM
killer, a `kill`) is made again and its mutant run again, twice at most; then the mutant
is "crashed", not counted, and the run is not recorded. The cost: N copies of the tree
(about 90 MB each, its bytecode compiled in about 3 s) and N test processes at once, a
golden book's `tjs run` among them, so N free cores and the memory for them. What it
buys, measured on the 5d proof (11 mutants of Coinbase and of the cash readers, a
20-core machine at a load of about 4): `--jobs 3` took 834 s and 535 s against 1378 s
and 1000 s at `--jobs 1`, with the same report byte for byte; a mutant takes as long in
either, and the control runs once. 150 sites of Coinbase or IB at `--jobs 3`: about 1.5
hours with 10% survivors, 3 hours with 27%, the control included.

Record a run from a landed commit: the record names the commit and its tree, and a
rebase-merge gives a landed commit a new id but keeps the tree, which the test checks (a
commit's whole tree, never a folder's). Run it on a clean checkout of that commit, or a
copy of it (its history fetched, or `--commit SHA` naming a commit the copy holds), with
the dev venv linked as `venv`, alone, on a quiet machine: a tree that is not its
commit's (a file git ignores below src/, tests/ or scripts/ included, the tools' caches
apart) is refused, unless `--no-record` (a proof; no record is printed). `--shard K/N`
splits a sample over N copies (the first traces with `--trace-only --jobs J`, the others
reuse its `--trace-file`); a part prints no record: only a whole run is recorded, and
`--jobs` runs one in parallel. The trace of every test module took 48 minutes at `--jobs
3` (the 5e measurement, a 20-core machine at a load of 10 to 50). Record each run in
`scripts/mutation-runs.json` after its triage.

## Adding a brokerage parser

1. Subclass `BaseBrokerage` in `src/taxjson/lib/brokerages/your_broker.py` and
   fill the parse template's phases (`src/taxjson/parsers/protocol.py`):
   `read(path)` returns the rows (each carrying its export line as `line`),
   `classify(row)` an `Outcome` (skip, nonevent, reported, consumed or build),
   `build(row)` the row's transactions (an expiry kept in `self._expiries`
   for the settlement clamp; a parser whose row undoes rows built before it
   hands its own list in `new_transactions()`, as Interactive Brokers
   does), `finish(transactions, path)` the pairings and
   notes after the loop, `after_marks(transactions)` what must follow the
   template's split-fill marks (an output order, a costing the marks must
   not see). A skip is `Outcome.skip` (the summary lists it as
   unclassified), `Outcome.nonevent` (a recognized non-event, listed
   calmly) or `Outcome.reported` (your own note already reports it:
   counted, not listed); `parse_file` is
   the base's template over them and runs the closing steps every parser
   shares. Reuse the `base.py`
   helpers (option symbol formatting, currency suffix, fee back-compute,
   dividend qty/rate extraction, `tx_roc_adjust`) instead of reinventing them,
   and follow the schema in `src/taxjson/lib/brokerages/schema.py` — it is
   enforced: `taxjson-brokerage` validates every parse against it.
2. Count what you drop, through the template: a row the parser can't classify is
   answered `Outcome.skip('<category>')` (see the Questrade/RBC/Coinbase
   `classify`); the template counts each row once, so a hook never calls
   `count_skip` for a row it answers, and a move the finish makes is `recount`.
   `taxjson-brokerage --lint` reconciles rows in vs rows out.
3. Add the parser to `src/taxjson/parsers/registry.py`: one `ParserSpec`
   (its id, the other spellings `taxjson-brokerage --brokerage` accepts,
   the module and class, the tier, the name of its content detector,
   the file-name prefixes and words of the name fallback). A parser
   missing there is not a parser: the detection tables and the
   brokerage tool's registrations are generated from the list. Define
   the content detector in `src/taxjson/lib/brokerages/detect.py` under
   the name the spec gives, built on your parser's OWN header test
   (export the function the parser uses, as `questrade.missing_columns`
   or `kraken.header_kind` do) — never a second column list, and never
   the file name. Add your samples to the detector matrix in
   `tests/features/discovery/test_detection_precedence.py`: each must match only its own detector.
4. Add a SYNTHETIC fixture (never real account data) at
   `tests/fixtures/your_broker/sample.csv`, register a three-line subclass in
   `tests/test_parser_conformance.py`, and mint the golden with
   `UPDATE_GOLDEN=1 python -m unittest tests/test_parser_conformance.py` (it writes
   `expected.json`, and `expected_cash.json`, the cash events your broker's parse
   builds of the sample: `[]` for none).
   The conformance kit (schema, golden, determinism, skip accounting, the account phase's
   signatures, detection through
   `tjs run`'s routing, the schema and the accounting under each country, the same
   rows under each country at one basis) plus a
   few behavior-specific unit tests is the review bar. A parser whose rows depend on a
   basis field (the stablecoin model: Kraken, Coinbase) names it in its class's
   `BASIS_INPUTS`; under any other field's flip the kit wants the same rows. A second export's fixture is a
   folder of its own whose `fixture.toml` says `parser = "your_broker"`. The folder's
   `fixture.toml` claims the kinds its rows have, `kinds = "trade, expiry, dividend"`
   (`taxjson.parsers.protocol.ROW_KINDS`, which `row_kind` reads off a row), and those of
   the cash events your broker's parse builds of it, `cash_kinds = "CASHMOVE"`
   (`CASH_KINDS`; `""` for none): the kit asks for a row, or an event, of each claimed
   kind, and a row or event of no kind is a finding. A folder without the claims passes,
   and `tjs parser-check` says it claims none. Each builder declares the kinds it books,
   `@builds("trade", "expiry")` (`taxjson.parsers.protocol.builds`; on `finish` or
   `after_marks` too when they book rows of their own): a row of a kind its parser does not
   declare is a finding, and so is a declared kind none of the parser's folders claims
   (`parser_kinds` is the union). An account setting the parse reads (Webull's
   `exercise_fee = 5.00`) goes in `fixture.toml` too: every parse of the sample is told it. Your `build`
   returns rows or an `Outcome`, never counts: the template counts each row once and
   refuses a build that counted its row itself. The template's surface is
   `taxjson.parsers.protocol.SURFACE` (the hooks, the account phase, what the tools read,
   the counting, the settings, the shared readers): a hook or helper your parser needs
   that is not there is a change to the protocol, reviewed as one. An account phase of
   your own (`prepare_files`, `coverage_messages`, `reconcile_files`) keeps the base's
   signatures: `prepare_files` takes `tax_year`, `combined`, `taxable`, `symbol_codes`
   and `project` by keyword, whichever it reads.
5. `taxjson-generate-parser` can draft step 1 from a CSV sample — its prompt
   is generated from the same schema table, but the draft still goes through
   steps 2–4 like hand-written code.

## Adding a broker fetcher (`tjs fetch`)

Broker API clients never go in the core: the core holds no broker
client and reads no broker credential (SECURITY.md lists the core's
network egress). `tjs fetch` is a thin dispatcher over plugins, so a
new broker's downloader is its own package. The contract is the module
docstring of `src/taxjson/lib/fetchers.py`; in short:

1. Write a class (instantiated with no arguments) or an object with:
   - `brokerages` — the `brokerage = "..."` values (under
     `[accounts.<name>]` in taxjson.toml) it serves;
   - `description` — one line for `tjs fetch --list`;
   - `fetch(request) -> {account: {...}}` — download every account in
     `request.accounts` into `request.inputs / <account>` (the inputs
     folder: with `[settings] inputs_dir` it is the folder of exports
     every year's project shares, and `request.shared_inputs` is true),
     in a format an existing parser reads (or a `.tt` file), and return a
     JSON-able result per account (the `--json` document). A positions
     snapshot goes in `request.holdings` (the year's holdings folder).
     An older core sends no `inputs` / `holdings`: fall back to
     `request.root / "inputs"` / `request.root / "holdings"` then. Fail
     with `SystemExit("tjs fetch: ...")`, never a traceback.
   - optional: `add_arguments(parser)` (its own `tjs fetch` options;
     an option two fetchers both define is shared, so keep the
     conventional meaning, e.g. `--year`), `account_keys` (extra
     `[accounts.<name>]` keys the config check should accept; `brokerage`,
     `account` and `query_id` always are) and `setup_hint` (shown when no
     account declares one of its brokerages).

   `request` (`FetchRequest`) also carries `root` (the year's project),
   `work`, the parsed `config`, the parsed `args`, a `say` progress
   printer (stderr under `--json`) and the `dry_run` / `json` flags.
2. Register it in your package's `pyproject.toml`:
   `[project.entry-points."taxjson.fetchers"]` → `mybroker =
   "mybroker_fetch.plugin:Fetcher"`, and depend on `taxjson`.
3. Test it offline: inject the HTTP layer (see
   `packages/taxjson-fetch/src/taxjson_fetch/api.py`'s `http_get`) and
   round-trip the written file through the core parser. Never commit a
   real token or account number.

`packages/taxjson-fetch` (Questrade REST API, IBKR Flex) is the worked
example; a fetcher kept in this repository lives under `packages/` and
`scripts/ci.sh` runs its tests. User setup of the plugin is in
[docs/brokers.md](docs/brokers.md).

## Running the stages by hand

`tjs run` drives the whole pipeline for a project. The stage tools
it calls are console scripts too — handy for one file, scripting, or
seeing what a parser makes of an export. Each takes `--help`; outputs are
JSON that feed the next stage. A walk-through on the demo files is in
[examples/README.md](examples/README.md).

```bash
export TAXJSON_LOCAL_TZ=America/Toronto   # crypto exports: UTC times dated in this zone (required for Kraken/Coinbase)

# 1. Convert a broker CSV to normalized JSON. --country picks the one
#    country-specific parse choice (IB foreign return of capital: ITA
#    s.90(1) dividend in Canada, a basis reduction in the US).
taxjson-brokerage --brokerage ib --account margin --country ca activity.csv > margin.json

# 2. Merge each ACCOUNT'S files into one per-account JSON. Sheltered
#    accounts are NOT merged into the taxable input: RRSP/TFSA/IRA trades
#    are outside the taxable ACB/FIFO pool and go to taxjson-gains via
#    --sheltered (so they still feed cross-account loss-denial checks).
#    --dedup / --validate are opt-in; without them merge2 just concatenates.
taxjson-merge2 --dedup --validate margin.json > taxable.json
taxjson-merge2 --dedup            rrsp.json   > sheltered.json

# 3. Compute gains for a tax year. --taxable turns on superficial-loss /
#    wash-sale detection (ITA s.54 and s.40(2)(g) in Canada, IRC §1091 in
#    the US); --sheltered passes registered-account context for
#    cross-account matching. A Canada project uses grant timing for
#    written options: pass --option-premium-timing grant
#    --option-grant-since YEAR to match `tjs run`.
taxjson-gains --country ca --year 2025 --taxable \
    --sheltered sheltered.json taxable.json > gains.json

# 4. Summarize
taxjson-sum-gains gains.json        # realized gains/losses
taxjson-sum-income taxable.json     # dividends/interest (reads merged JSON)
```

These stages skip what `tjs run` adds around them (currency
conversion to the base currency, corporate-action elections, ticker.map,
the cross-account wash pass, missing-history openings), so their figures
are not the project's.

## Adding a country rule set

Country-specific cost-basis and wash-sale logic lives in `src/taxjson/engine/` (`ca/` and `us/`, beside the modules both share). The Canadian ACB and US FIFO paths are the templates; add a new branch keyed off the `--country` CLI flag and write tests that exercise the boundary conditions (year-end carryover, multi-account aggregation, settlement-date edge cases).

## Canada and US law never mix

- **One country resolver.** Read a project's or a flag's country only
  through `src/taxjson/lib/country.py` (`canonical_country`,
  `settings_country`, `country_arg`, `resolve_tax_date`,
  `home_currency`). The parse, characterize and the engine read its leaf,
  `src/taxjson/rules/country.py` (`canonical_country`, `parse_basis`,
  `foreign_roc_choice`), which imports nothing from taxjson. A missing or
  unknown country is an error there, never a silent Canada.
- **Ownership tables.** A setting, config table, engine flag or command
  that belongs to one country goes in `SETTING_COUNTRY`,
  `CONFIG_COUNTRY`, `FLAG_COUNTRY` or `COMMAND_COUNTRY` (with its
  `*_WHY` reason). The config readers, the engine CLIs and `taxjson`'s
  dispatch refuse the other country's entries from those tables; tax-logic
  states them.
- **Country gates live in the engine, command or config layer.** Parsers
  emit neutral facts; they never decide one country's tax treatment.

## tax-logic is the spec

Every statement `tjs tax-logic` prints is a `Rule` with a stable id in
`src/taxjson/lib/tax_logic.py` (`CA-...` in the Canada section, `US-...` in
the US section; `tjs tax-logic --ids` shows them). Each id is a constant in
`src/taxjson/rules/ids.py` (`CA_SL_02 = "CA-SL-02"`); tax_logic names it by
that constant (`from taxjson.rules import ids as R`, then `R.CA_SL_02`), and
so may code that applies the rule, in any layer; in src it is never a bare
string (a message may quote it). A change in tax logic changes or adds a Rule in the right
country's section (a new one adds its constant first), the code that applies
it cites it (the constant, or the id in a comment), and the test that pins
it cites the constant (a string id fails `scripts/check_tax_rules.py`), through the
module's own import of the ids, `R` (or `RID` where the module's `R` is something else):

```python
from taxjson.rules import ids as R
from tax_rules import rule, rule_absent
from tax_rules.dual import gains_both, tx

@rule(R.CA_SL_02)                         # Canada applies it ...
@rule_absent(R.CA_SL_02, country="usa")   # ... the same book under the US does not
@rule(R.US_WASH_06)
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
`baseline-unpaired.txt`, a `[settings]` key no rule names, `rules/ids.py`
out of step with the statements, a rule id written as a string (or built
by an f-string, a format string or a %-string) outside `rules/ids.py`, an
import of the ids other than `from taxjson.rules import ids as R`, a cited id (in a comment, a docstring or a message) or an `R.` constant that
is no rule or a retired one, and a rule cited nowhere in src outside the
spec beyond `baseline-uncited.txt`. When a rule gains a test or a citation,
delete its baseline line (for `baseline-uncited.txt`, lower `UNCITED_MAX`
too); a new rule can never be baselined (each baseline holds only ids of
its seed file). Retire an id in `tests/tax_rules/retired.txt` (and take its
constant out of `rules/ids.py`); never reuse one. `scripts/check_tax_rules.py
--where CA-SL-02` (or a family, `--where CA-SL`) lists the tests that pin a rule,
wherever they sit.
The tests are moving into packages by rule family (`tests/rules/ca/sl/`) and by
feature (`tests/features/<area>/`, an area of `FEATURE_AREAS` in
tests/test_test_layout.py, one per section of docs/architecture-map.md);
`scripts/moved.py test_fix_x` says where a module the backlog or the CHANGELOG
names went (its docstring ends `Formerly: test_fix_x`).

## Pull request checklist

Open pull requests here as usual; CI runs on them. This repository's
`main` changes only through releases, so a pull request is not merged
here: once it is ready, the maintainer brings it into the development
repository (you stay the commit's author), and it ships with the next
release; the pull request is then closed with a note saying which.

- [ ] `scripts/ci.sh` passes — read its **last line**: it must say `PASS`
      (a pipe such as `scripts/ci.sh | tail` hides the exit status).
      `scripts/ci.sh --quick` while iterating; the full gate before you
      push, once in a fresh `git clone` (it catches untracked files). The
      clone has no venv: run `scripts/dev-setup.sh` there, or test its code
      with your clone's venv:
      `PYTHONPATH=$PWD/src PYTHON=<your clone>/venv/bin/python3 scripts/ci.sh`.
- [ ] New behavior has a test that would have failed before the change
- [ ] A tax change edits its `Rule` in `src/taxjson/lib/tax_logic.py` (a new
      id gets its constant in `src/taxjson/rules/ids.py`), the code that
      applies it cites it, and the test cites it (`@rule(R.CA_...)`)
- [ ] Docs kept true: a user-visible fix adds a `CHANGELOG.md` bullet under
      `## Unreleased` and its `docs/troubleshooting.md` entry (Fixed in:
      `unreleased`); moved code updates `docs/architecture-map.md`
- [ ] Synthetic data only; no personal data in the change or its commit
      messages (the pre-push hook runs `scripts/check-pii.sh`)
- [ ] No unrelated drift (no formatting churn, no rename cascades)
- [ ] PR description explains *why*, not just *what*

## License

By contributing, you agree your contributions will be licensed under the MIT License.
