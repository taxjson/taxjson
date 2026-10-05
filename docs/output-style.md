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

## Numbers, dates

- Money: `fmt_money` — thousands separators, two decimals, `-` for a
  negative, no currency symbol (the currency is in the title or a
  column). Quantities: `fmt_qty`. Per-share and per-unit figures may keep
  four decimals.
- Numbers right-align in tables (`fit_table` does it for every all-numeric
  column).
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
