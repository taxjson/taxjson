# Output style

How taxjson prints to people. One module implements it,
`src/taxjson/lib/out.py`; a command's text output goes through it, and its
tests check the result with `out.lint()`.

## Width

- Prose wraps at **100 columns** when the output is not a terminal (a
  pipe, a file, a test), and at **min(terminal width, 100)** on a
  terminal (never narrower than 40).
- `TAXJSON_WIDTH=N` sets the width; `TAXJSON_WIDTH=0` turns wrapping off.
- Words, hyphenated ids and `` `code spans` `` are never broken. A command
  the reader is meant to copy is printed on its own line and never
  wrapped, however long (`out.Verbatim` in a key/value block).
- A table row is never wrapped. A table wider than the width first drops
  the columns the command marks least important (`fit_table(drop=...)`),
  then switches to a per-record layout: one block per row, its key
  column(s) first, the other cells as `label value` pairs under it.

Output **captured for a program** is never wrapped: inside
`out.unwrapped()` `width()` is 0. `lib/dispatch.run_cmd` enters it (and
sets `TAXJSON_WIDTH=0` for a subprocess) whenever it captures a tool's
stdout or stderr — a stage's work/ or reports/ file, the `.diag` the
`.sum` DIAGNOSTICS fold in — and `taxjson checklist` runs the commands
it reads with `TAXJSON_WIDTH=0`. So those bytes never depend on a
terminal, every line a program greps stays whole, and the checklist
still shows a failed command's whole last line. Whoever shows captured
text to a person wraps it then (the run's console echo of a stage's
ATTENTION lines, for one).

Text **shown to a person** (width > 0) never carries a control character
other than newline and tab: `out.wrap` (so `fill`, `message`, `emit`,
`note` ...), `fit_table`, `kv_lines`, `Doc` and
`stage_msg.console_lines` show each as the visible text `\xNN`
(`out.printable`; `out.shown(text, stream)` for a line printed as is),
so an ESC sequence in a broker symbol cannot drive the terminal.
Captured output (width 0) keeps its bytes.

## Structure

- A **title** line first: `WHAT — context` (`WASH SALES — USD, tax year
  2025, basis: wash-adjusted`). The words before the dash are upper case.
- **Sections** are separated by exactly **one** blank line and open with a
  short upper-case heading. Never two blank lines in a row, never a blank
  line first or last.
- Inside a block, sub-headings are sentence case (`Pool history (ACB
  trace)`), indented under their block.
- **Lists** are `- ` items with a hanging indent: the second line starts
  under the text, not under the dash.
- **Key/value blocks** align their values: `label:  value`, the labels
  padded to the longest; a long value wraps under itself.
- The **last line** of a listing that asks for action says what to do, in
  one line (`taxjson checklist` shows a command's last line as its
  detail).

## Messages

Diagnostics go to stderr; report content goes to stdout. One set of
prefixes, lower case, GNU style (`<prog>: <kind>: ...`, lib/cli_diag):

| prefix | meaning |
| --- | --- |
| `error:` | the command could not do what was asked (non-zero exit) |
| `warning:` | the result may be wrong or incomplete; act on it |
| `warning: ATTENTION: <topic>:` | the run's must-act channel (see below) |
| `note:` | information; nothing to do |

A long message is a **one-line headline** (what happened, to what) and
**indented detail lines** (why, and the fix) — not a 400-column line.
The headline carries the words a reader (or a grep for the marker) needs:
the detail lines are its continuation, indented two spaces.

Retired: `NOTE:`, `Note:`, `WARNING:`, `Warning:`, `!!`, `→`/`->` bullets,
`*** ... ***`. Upper-case words stay where they are **values** — a status
column (`WARN`, `ERROR`, `OK`), a checklist mark (`[x]`, `[!]`).

```python
from taxjson.lib import out
out.warn("fmv_per_share=0 books this taxable_deemed_dividend at $0",
         prog="taxjson elect",
         details=["No income and a $0 cost for the new shares. ..."])
out.fail("'X' matches no pending event — nothing was saved",
         prog="taxjson elect", details=["Check the id with ..."])
```

`out.fail(..., code=1)` raises `SystemExit(<text>)` as `sys.exit(msg)`
does (in-process callers read the text from the exception); `code=2` (a
usage or input error) prints and exits 2.

The shared helpers already speak it:

- `taxjson_run._die(headline, *details)` (exit 1) and
  `_die_input(headline, *details)` (exit 2) print `taxjson <cmd>: error:
  <headline>` and the details indented. An old one-string call still
  works (the string wraps under its prefix); when you touch one, split it
  into a short headline and details.
- `lib/cli_diag.warn / note / error(prog, msg, details=())` print
  `<prog>: warning: ...` the same way.
- `_wrap_note` wraps at the house width (it was 78).

Not yet converted, and each group's to do: the direct `sys.exit(f"taxjson
<cmd>: ...")` calls, bare `print(f"warning: ...")` lines, and the
`NOTE:` prints.

## Numbers, dates

- Money: `fmt_money` — thousands separators, two decimals, `-` for a
  negative, no currency symbol (the currency is in the title or a
  column). Quantities: `fmt_qty`. Per-share and per-unit figures may keep
  four decimals.
- Numbers right-align in tables (`fit_table` does it for every all-numeric
  column).
- Tables: two-space gaps. `fit_table(headers, body, drop=, key=)` for
  structured cells; for the views built as space-joined rows,
  `format_report_table(rows, fit=True, drop=(...), key=(...))` (and
  `_print_report_table(..., fit=True, ...)`) is the same table. Without
  `fit=True` a report table keeps its old layout (three-space gaps, never
  fitted): opt in per table, after checking no program reads its rows.
- Dates are ISO, `YYYY-MM-DD`; a range is `FROM to TO`.
- Paths inside the project are shown relative to it (`out.relpath`).

## Never changed by a style pass

These are read by programs or compared by users; a restyle leaves their
bytes alone:

- every `--json` output, and the other machine stdout modes: `events
  PERIOD ACCOUNT` (re-importable `.tt` lines), `form-export --form txf`
  and `--out`/`--csv` files;
- the files the run writes under `work/` (books, `*.diag`, the
  pending-elections document, fingerprints) and `inputs/` (`manifest.json`,
  `sends.json`, `crypto_sends.tt`, `opening_*.tt`, drafts);
- `reports/`: `<account>.sum` / `_wash.sum` (users diff them across runs
  and years), `*_holdings.toml` (read by `sanity` and the holdings diff),
  `wash_radar_*.json`, `run_summary.json`; `filed/<year>.json`;
- exit codes;
- the marker lines the run reads back from its stages' stderr, with their
  exact first-line prefix: `warning: ATTENTION:` (and the topics
  `short:`, `opening:`, `crypto id:`, ...), `warning: UNBOOKED:`,
  `warning: <file> parsed to 0 transactions`, the `  <file>: N tax
  objects` count lines. The DIAGNOSTICS collector keeps a line matching
  `(prog: )?(ok|warning|note|error|validation):` plus the indented lines
  that follow it — so a wrapped message keeps its marker on the first line
  and indents the rest;
- the text `taxjson checklist` reads from other commands: `sanity` (lines
  starting `INCOMPLETE` / `UNCHECKED`, the last line), `find-missing-history`
  (the `AFFECTS` / `REMOVE from ` sections and their row shape),
  `elect --pending` (`No pending elections`, the last line), `audit`
  (`pipeline tie-out N tied, N MISMATCHED, N not found`), `fx-cash` and
  `check-filed` (`DRIFTED`, the last line), and the last stderr line of a
  command that failed.

## Tests

Pin the layout, not the wording, with `out.lint(text, width_=100,
allow=(...))`: no prose line over the width (table rows and lines
containing an `allow` substring — a command to copy — are exempt), no
blank-line runs, no leading/trailing blank line, no retired prefix. Pin
the meaning with the phrases a reader needs; a phrase may wrap, so compare
against `" ".join(text.split())` when it is long.

- The suite runs with `TAXJSON_WIDTH=0` (`scripts/ci.sh`, `run_tests.sh`),
  so a phrase a test looks for never depends on where a temp path made a
  message wrap. Run single modules the same way, or flatten the text.
- Style tests set their own width: `tests/_style.py` builds the synthetic
  style projects (`tests/fixtures/style/{canada,usa}`: options,
  superficial losses, a spin-off, crypto with a send, dividends,
  transfers, a sale with no purchase, slips, holdings, a price cache) once
  per process and runs a command as a pipe would (width 100):

  ```python
  from _style import project, assert_styled
  r = project("canada").run("harvest", "--no-ibkr", "--options")
  assert_styled(self, r.stdout)              # out.lint at width 100
  ```

  `project(country, pending=True)` stops at the pending spin-off
  election. Add each converted command to
  `tests/test_style_smoke.py::TestConvertedCommands.CASES`.
- Re-measure every command: `python3 scripts/style/survey.py OUTDIR`
  (captures, both countries), then `python3 scripts/style/measure.py
  OUTDIR` (worst first).
- Synthetic amounts in docs, README, comments and fixtures stay under
  1,000 (or carry a `pii-ok` marker), in the commit that adds them: the
  pre-push check refuses an amount with thousands separators and cents.
