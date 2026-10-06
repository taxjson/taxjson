# taxjson: notes for AI assistants

taxjson computes capital gains, adjusted cost base (ACB), income and the
superficial-loss rule from broker CSV exports (Questrade, RBC Direct,
Interactive Brokers, Webull, Coinbase, Kraken, or a generic mapping). Canada
first; the US engine (FIFO basis, wash sales) is experimental. It runs on the
user's own machine. Python 3.9+, `src/taxjson/`; the CLI is `taxjson` (`tjs`).
Claude Code, Codex, Gemini CLI, Cursor and Copilot read this file.

## Helping someone use taxjson

1. **Run the checks first, in their project folder:** `tjs checklist`,
   `tjs sanity` (positions against the broker's holdings) and
   `tjs find-missing-history`. The console summary at the end of `tjs run`
   lists what to look at. Most problems are an input (a missing older export,
   a transfer in, an election not made), not a bug. Say which it is.
2. **`docs/troubleshooting.md`: known problems.** Search it for the exact
   message text. Each entry says how to **check** it is that problem, the
   cause, the fix, the release it was **fixed in** (newer than theirs, from
   `tjs --version`: the fix starts with upgrading) and the code.
3. **Settings:** `docs/settings.md` (every taxjson.toml key; `tjs format`
   lays the file out). **Tax rules:** `docs/tax-rules.md`; the spec is
   `tjs tax-logic` (`--ids` shows each rule id).
4. **Where the code is:** `docs/architecture-map.md`. Don't read
   `src/taxjson/bin/taxjson_run.py` (22k lines) top to bottom: search it for
   the `cmd_<command>` or `stage_` name the map gives.

**Privacy.** Work from tjs command output. Never read the raw CSVs in a
user's `inputs/`. Never put their amounts, account numbers or names anywhere
(issues, commits, docs, chat logs) without asking. To share a sample,
`tjs redact` copies `inputs/` to a redacted `inputs_redact/`; review it first.

**No tax advice.** taxjson is a calculator that shows its work. Explain what
it computed and which rule it applied; for what to file, point to the CRA or
IRS guidance and a tax professional.

## Changing the code

- **Set up:** `scripts/dev-setup.sh` (venv, editable install, ruff, the
  pre-push hook), then `source setup.sh`.
- **The gate:** `scripts/ci.sh` (lint, consistency, tax-rules, PII scan,
  suite, fuzzers); `scripts/ci.sh --quick` skips the fuzzers. Read its last
  line for PASS: a pipe (`| tail`) hides a failing exit status. One module:
  `cd tests && TAXJSON_WIDTH=0 python -m unittest test_x`. Before pushing,
  run the gate once in a fresh `git clone` (it catches untracked files).
- **Releasing:** `scripts/release.sh vX.Y.Z` (CHANGELOG `## Unreleased` →
  `## vX.Y.Z (date)`, full gate, tag, push), then `scripts/promote.sh vX.Y.Z
  beta` and later `scripts/promote.sh vX.Y.Z` for stable. Channels: latest,
  beta, stable (new installs), dev. See `docs/releasing.md`.
- **Conventions the tests and hooks enforce:**
  - Canadian and US law never mix. Read the country only through
    `src/taxjson/lib/country.py`. `tjs tax-logic` is the spec: a tax change
    edits its `Rule` in `src/taxjson/lib/tax_logic.py`, and the test cites
    it with `@rule("CA-...")` (`tests/tax_rules/`; checked by
    `scripts/check_tax_rules.py`).
  - No personal data in the repo: the pre-push hook runs
    `scripts/check-pii.sh`. A money amount with thousands separators and
    cents added to a doc, CHANGELOG or comment needs to stay under 1,000 or
    carry a `pii-ok` marker on its line.
  - No hard-coded security data (coin ids, tickers, ratios): it goes in the
    user's ticker.map or taxjson.toml, with a clear error naming the line
    to add.
  - Output follows `docs/output-style.md`: `Info:` / `Warning:` / `Error:`
    labels, `==>` steps; captured `.diag` / `.sum` / work/ bytes stay
    stable (`TAXJSON_WIDTH=0`).
  - User files (taxjson.toml, ticker.map, .tt) are written through
    `src/taxjson/lib/safe_write.py`.
  - Synthetic test data only (ids like `U1234567` or `99900001`, made-up tickers).

## The knowledge pack

`docs/troubleshooting.md`, `docs/architecture-map.md`, `docs/tax-rules.md`,
`docs/settings.md`. Keep it true as you work: a fix for a problem users can
hit adds or updates its troubleshooting entry; moving or renaming code the
map names updates the map. A code reference is written
`` `path` — `symbol` `` or `` `path` — `sym1`, `sym2` `` (repo-relative path;
each symbol an identifier in that file or a literal fragment of it).
`tests/test_knowledge_pack.py` fails when the pack names a missing file or
symbol, a `tjs` command that does not exist, a "Fixed in" that is not a
CHANGELOG release, or anything that looks like personal data.
