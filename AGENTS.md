# taxjson: notes for AI assistants

taxjson computes capital gains, adjusted cost base (ACB), income and the
superficial-loss rule from broker CSV exports (Questrade, RBC Direct,
Interactive Brokers, Webull, Coinbase, Kraken, or a generic mapping). Canada
first; the US engine (FIFO basis, wash sales) is experimental. It runs on the
user's own machine. Python 3.9+, `src/taxjson/`; the CLI is `taxjson` (`tjs`).
Claude Code, Codex, Gemini CLI, Cursor and Copilot read this file.

## Helping someone use taxjson

1. **Start with `tjs checklist` in their project folder:** every step
   from install to filing, each checked, the next one named with its
   command. Then the detail behind a step: `tjs sanity` (positions against
   the broker's holdings), `tjs find-missing-history` and, in Canada,
   `tjs slip-audit` (T5 / T3 slips). The end of `tjs run` lists what to
   look at. Most problems are an input (a missing older export, a transfer
   in, an election not made), not a bug. Say which it is. A command's
   default output is the essentials; `--details` shows the rest.
2. **`docs/troubleshooting.md`: known problems.** Search it for the exact
   message text. Each entry says how to **check** it, the cause, the fix,
   the release that **fixed** it (newer than `tjs --version`: upgrade) and
   the code. Bugs an upgrade alone fixes, and breaking changes:
   `docs/upgrading.md`.
3. **Settings:** `docs/settings.md` (every taxjson.toml key). **Tax
   rules:** `docs/tax-rules.md`; the spec is `tjs tax-logic` (`--ids`).
   Commands: `docs/commands.md`; downloads: `docs/brokers.md`; out of
   scope: `docs/limits.md`; terms: `docs/glossary.md`; a first project:
   `docs/getting-started.md`; the year end: `docs/filing.md`.
4. **Where the code is:** `docs/architecture-map.md`. Don't read
   `src/taxjson/bin/taxjson_run.py` (28k lines) top to bottom: search it for
   the `cmd_<command>` or `stage_` name the map gives.

**Privacy.** Work from tjs command output. Never read the raw CSVs in a
user's `inputs/`. Never put their amounts, account numbers or names anywhere
(issues, commits, docs, chat logs) without asking. `tjs redact` copies
`inputs/` to a redacted `inputs_redact/` (it keeps amounts): review it first.

**No tax advice.** taxjson is a calculator that shows its work. Explain what
it computed and which rule it applied; for what to file, point to the CRA or
IRS guidance and a tax professional.

**Reporting a bug** (`.github/ISSUE_TEMPLATE/bug_report.md`):
- Include: `tjs --version`, Python, the OS, the command, its `Error:` /
  `Warning:` lines, and the `tjs checklist` and `tjs sanity` summaries.
- Never include real amounts, account or slip numbers, names, the raw CSVs
  or anything copied from them.
- Reproduce it on a made-up CSV: copy the broker's `examples/*_demo.csv`
  (or `tjs init --demo DIR`), edit rows to the same shape (columns, actions,
  wording; made-up values and ids), confirm it fails the same way, and
  attach that file. A `tjs redact` copy is a fallback, attached only after
  the person has read all of it.
- Show the person the full report first; then file it with
  `gh issue create --repo taxjson/taxjson`, or give them the text.

## Changing the code

- **Set up:** `scripts/dev-setup.sh` (venv, editable install, ruff, the
  pre-push hook), then `source setup.sh`.
- **The gate:** `scripts/ci.sh` (lint, consistency, tax-rules, PII, suite
  in parallel, no-extras, fuzzers; `--quick` skips the fuzzers).
  Read its last line for PASS: a pipe hides the exit status. One module: `cd
  tests && TAXJSON_WIDTH=0 python -m unittest test_x`. Before pushing, run
  the gate once in a fresh `git clone` (it catches untracked files).
- **Releasing:** `scripts/release.sh vX.Y.Z [--notes FILE]` (gate, tag, push,
  GitHub release with scanned notes; never `git push --tags`), then
  `scripts/promote.sh vX.Y.Z beta` / `vX.Y.Z` (stable; needs green GitHub
  CI). Channels: latest, beta, stable (new installs), dev. See `docs/releasing.md`.
- **Conventions the tests and hooks enforce:**
  - Canadian and US law never mix. Read the country only through
    `src/taxjson/lib/country.py`. `tjs tax-logic` is the spec: a tax change
    edits its `Rule` in `src/taxjson/lib/tax_logic.py`, and the test cites
    it with `@rule("CA-...")` (`tests/tax_rules/`; checked by
    `scripts/check_tax_rules.py`).
  - No personal data in the repo (pre-push: `scripts/check-pii.sh`). A money
    amount with thousands separators and cents added to a doc, CHANGELOG or
    comment stays under 1,000 or carries a `pii-ok` marker on its line.
  - No hard-coded security data (coin ids, tickers, ratios): it goes in the
    user's ticker.map or taxjson.toml, with an error naming the line to add.
  - Output follows `docs/output-style.md`: essentials first (a legend, the
    data, one-line `! ` items; the rest behind `--details`; `--json`
    unchanged); `Info:` / `Warning:` / `Error:` labels, `==>` steps; a
    message's later lines flush-left, one blank line after a message of more
    than one line (`out.show` / `out.emit`, not a loop of `print`); width
    120 piped, the terminal's up to 160; captured `.diag` / `.sum` / work/
    bytes stay stable (`TAXJSON_WIDTH=0`).
  - User files (taxjson.toml, ticker.map, .tt) are written through
    `src/taxjson/lib/safe_write.py`.
  - Child Python: `lib/dispatch.py` `python_module_argv` (`-P`), never
    `[sys.executable, "-m", ...]` (imports a planted `json.py` from the
    cwd); in a project run `tjs`, never `python -m taxjson...`.
  - Synthetic test data only (ids like `U1234567` or `99900001`, made-up tickers).
  - A test's temp file goes in a folder of its own
    (`tests/_tmpfiles.py` `private_tmpfile`, or `tempfile.TemporaryDirectory()`),
    never the shared temp root: parsers read the broker CSVs beside a file.

## The knowledge pack

`docs/troubleshooting.md`, `docs/architecture-map.md`, `docs/tax-rules.md`,
`docs/settings.md` and the user pages above. Keep it true: a fix for a
problem users can still hit adds or updates its troubleshooting entry
(Fixed in: `unreleased`; the release script fills it in); one only an
upgrade fixes gets a line in `docs/upgrading.md`; moved code updates the
map. A code reference: `` `path` — `sym1`, `sym2` `` (repo-relative path;
each symbol an identifier or literal fragment in that file).
`tests/test_knowledge_pack.py` fails on a missing file or symbol, an
unknown `tjs` command, a "Fixed in" that is not a CHANGELOG release, or
anything that looks like personal data.
`tests/test_settings_doc.py` fails when a taxjson.toml key, a project-file
keyword or a tax-logic rule is missing from settings.md or tax-rules.md.
