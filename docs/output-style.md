# Output style

How taxjson prints to people. One module implements it,
`src/taxjson/lib/out.py`; a command's text output goes through it, and its
tests check the result with `out.lint()`.

## Essentials first

A command's default output is the essential information, said
concisely. It is not a place for every exception and caveat: a reader
who wants the finer points asks for them (`--details`, the topic
command, the docs). Every command follows these rules.

1. **Legend before the data.** A table that needs explaining gets a
   legend of **one or two short lines directly above it**: what the
   columns mean, nothing else. No tax theory, no edge cases, no "how
   we got here".

   ```
   REALIZED = NON-OPT (shares, units, futures, crypto) + OPTION; TOTAL = REALIZED + DIVIDEND + PIL.
   ACCOUNT  NON-OPT  OPTION  REALIZED  ...
   ```

2. **After the data, only what the reader must act on or must not
   miss**, each on **one line** naming the command that has the detail.
   A line the reader must act on starts `! ` (`out.act(text, cmd)`); a
   line they must not miss but need not act on has no prefix. Nothing
   else after the data: no prose paragraph, no list of caveats. The last
   line may point at the long form (`out.details_hint`):

   ```
   ! 2 sales with no purchase in your files are NOT in these totals — tjs find-missing-history
   Not in the rows: capital gains on T3/T5 slips (lines 17600, 17400) — tjs slip-audit
   More: tjs sum --details (notes)
   ```

   An `! ` line is never wrapped: keep it within 100 columns (name two
   or three items and `+N more`, never the whole list).
3. **The explanations move, they are not dropped.** Explanations,
   edge-case caveats, rounding notes, legal citations and per-item
   reasoning are printed with `--details` (every reworked command has
   it, and it prints everything the default view leaves out), or live in
   the per-topic command (`audit`, `wash-sales`, `fx-cash`, `form-export`,
   `find-missing-history`, `slip-audit` ...) and the docs.
4. **A budget.** The default output's non-table lines — every line that
   is not a table row or rule, a title or section heading, a `==> `
   step or a `label:  value` figure (`out.classify`; the budget counts
   `out.prose_lines`) — are at most **6**, stdout and stderr together.
   Where a command needs another budget it is listed here:

   | command | budget |
   | --- | --- |
   | `run` | each message one line (its headline; the detail with `--details` and in work/*.diag); the closing block under `==> Before you trust these numbers` at most 6 lines; `==> ` steps are not counted |
   | `checklist` | the next step, one `! ` line per item needing attention, one line per section with its counts (a table); at most 6 other lines. `--all` is the full list |
   | `format`, `format-map` | the diff is the data; the note after it one line |
   | `init --force` over another project | 8: the backup it kept and the leftover folders are never left out |
   | `tax-logic`, `help`, `-h` | exempt: they are the reference text |
   | `form-export --form txf`, `--json`, `events` `.tt` lines | exempt: machine output |

5. **Numbers the reader files stay on the default view.** A total for
   the return, a threshold result, an amount to enter: never behind
   `--details`.
6. **Every check that protects a filed figure stays**, as a one-line
   `! ` pointing at its detail: never silently dropped. A warning on
   stderr is its headline (one line) in the default view; its detail
   lines come back with `--details`. The messages a command's engine
   and stages print while it runs (a recomputed year's income-year
   notes, a built-in list note) are one line each, cut at the width
   (` ...`), and a message of one kind said more than twice is folded
   into one count line at the end (`Info: 7 more like "..." (--details
   shows each)`): `out.concise_show`, `out.fold_summary`, switched on
   by `taxjson_run._brief_messages` (run, redact and check-filed keep
   their own lines).

`--json` is unchanged by all of this (its schema is stable), and so is
the captured text other programs read (see "Never changed by a style
pass").

## Width

- On a terminal, prose wraps at the **terminal's full width, at most
  160 columns** (never narrower than 40). When the output is not a
  terminal (a pipe, a file, a test) it wraps at **120 columns**
  (`out.WIDTH`; `out.MAX_WIDTH` is the terminal cap).
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
- **Lists** in a document are `- ` items with a hanging indent: the
  second line starts under the text, not under the dash. (Inside a
  message they are flush-left: see Messages.)
- **Key/value blocks** align their values: `label:  value`, the labels
  padded to the longest; a long value wraps under itself.
- The **last line** of a listing that asks for action says what to do, in
  one line (`taxjson checklist` shows a command's last line as its
  detail).

## Messages

Diagnostics go to stderr; report content goes to stdout. Every message
a person sees **starts with its label**, capitalised:

| label | meaning |
| --- | --- |
| `Error:` | the command could not do what was asked (non-zero exit) |
| `Warning:` | the result may be wrong or incomplete; act on it |
| `Info:` | information; nothing to do |

The run's must-act channel (ATTENTION) is a `Warning:` to a person; the
word ATTENTION stays in the captured form only (`warning: ATTENTION:
<topic>: ...`, what the run and the `.sum` read back). Its topic is
shown capitalised (`out.ATTENTION_TOPICS`: `short:` is `Short
position:`, `income year:` is `Income year:` ...).

```
Warning: Short position: ABC.TO (margin) goes short on 2025-03-04
The books sell 10 more than they hold. ...

Error: no gains files in work/
Run `taxjson run` first.
```

Shown to a person, every line after a message's label line continues
it **flush-left** — the headline's wrap, a detail, a `- ` item and its
wrapped lines alike: no indentation (`out.message`,
`stage_msg.console_lines`). A message that takes **more than one line
is followed by exactly one blank line**; a one-line message, and a
`==> ` step, by none. The blank line is *owed*, not printed:
`out.show(lines, file)` prints a message and, when it spans more than
one line, the next text written to the same destination (a terminal, a
pipe, a file — stdout and stderr together when they go to one place,
apart when they do not) starts with the blank line. So the output never
ends with a blank line, there are never two in a row, and
`tjs run 2>err.txt` keeps each file clean on its own. At the top of a
process (`cli_diag.run_top_level`) `out.settling_streams()` makes every
write — a report's raw `print()` too — pay the debt first; a program's
own blank line pays it instead of adding a second. Several messages
built as lines at once: `out.join_blocks` / `out.show_blocks`.

**The source.** A message never starts with a program name. The
command the person typed is not named at all (`Warning: ...`, not
`taxjson sum: warning: ...`). A line captured from *another* program
and shown by the command that ran it — a child's error relayed in a
detail line — keeps that program's name right after the label, as its
source: `Error: taxjson-x: ...`. The one exception is `taxjson run`'s
console: its stages are its own parts, so a stage's line is shown
without the stage's name (`Error: 1 corp-action event(s) need an
election ...`; the .diag keeps it). Messages from a parser or the engine
name their file or account in the text, and the run shows them after
the account's step line.

**Shown vs captured.** The label is chosen in one place,
`lib/out.label` / `out.message` (and `out.relabel` for a captured line
shown later), keyed on the width: shown to a person (width > 0) the
label starts the line; captured for a program (width 0: a work/ or
reports/ file, a `.diag`, the text the run and `taxjson checklist`
read back, every subprocess under `TAXJSON_WIDTH=0`) the message keeps
its GNU bytes, `[<prog>: ]note: / warning: / warning: ATTENTION: /
error: ...` lower case — never changed by a style pass (see below).

A long message is a **one-line headline** (what happened, to what) and
**detail lines** (why, and the fix) — not a 400-column line. The
headline carries the words a reader (or a grep for the marker) needs:
the detail lines are its continuation, flush-left under it when shown
(two spaces in when captured, width 0).

Retired at the start of a line a person sees: the lower-case `note:`,
`warning:`, `error:` (the captured form), `NOTE:`, `Note:`, `WARNING:`,
`ERROR:`, a label behind a program name (`taxjson-x: Warning:`), `!!`,
`→`/`->` bullets, `*** ... ***`. Upper-case words stay where they are
**values** — a status column (`WARN`, `ERROR`, `OK`), a checklist mark
(`[x]`, `[!]`), `FAILED:` / `CAVEAT:` in an audit list.

```python
from taxjson.lib import out
out.warn("fmv_per_share=0 books this taxable_deemed_dividend at $0",
         prog="taxjson elect",
         details=["No income and a $0 cost for the new shares. ..."])
out.fail("'X' matches no pending event — nothing was saved",
         prog="taxjson elect", details=["Check the id with ..."])
```

`prog=` names the program in the captured form only. `out.fail(...,
code=1)` raises `SystemExit(<text>)` as `sys.exit(msg)` does
(in-process callers read the text from the exception); `code=2` (a
usage or input error) prints and exits 2.

The shared helpers speak it:

- `taxjson_run._die(headline, *details)` (exit 1) and
  `_die_input(headline, *details)` (exit 2) print `Error: <headline>`
  and the details under it, flush-left (`taxjson <cmd>: error:
  <headline>` and the details two spaces in, captured). An old
  one-string call still works (the string wraps under its label); when
  you touch one, split it into a short headline and details.
- `lib/cli_diag.warn / note / error(prog, msg, details=())` print
  `Warning: ...` / `Info: ...` / `Error: ...` the same way.
- `lib/stage_msg.say(kind, headline, details, legacy=...)` and
  `emit_line(text)`: a stage's message, its captured `legacy` one-line
  text kept byte for byte in the `.diag`, the label first when shown.
- A message written as text the old way: `emit_line("warning: ...")`
  for a line on stderr, `out.labelled(text)` for one in a report body,
  `out.label(kind, width)` to build one, `sys.exit(out.exit_text(
  "<prog>: <what>"))` for a refusal — each the captured bytes at width
  0, the label first when shown.
- An argparse usage error (`run_top_level`, every entry point) is
  `Error: <message>` under the usage line.
- `_wrap_note` wraps at the house width (it was 78).

## The run's console

What `taxjson run` prints for a person (width > 0, stdout and stderr) is
read by someone with little attention to spare. Every non-blank line
starts with exactly one of:

| start | meaning |
| --- | --- |
| `==> ` | a step the run is doing (`_step` in taxjson_run.py) |
| `Info: ` / `Warning: ` / `Error: ` | a message, the label at column 0 |
| anything else, flush-left | the continuation of the entry (step or message) on the line directly above |

A blank line comes **only after an entry of more than one line**, and
such an entry always has one after it (unless it is the last line): one
blank line, never two, never first or last. No indentation, no bare line
(a continuation with no entry above it), no lower-case label, no
`ATTENTION:` word, no stage program name (`taxjson-gains:` — the stage
is an implementation detail; its captured line in work/ keeps it), no
`(content: ...)` detection detail (work/`<acct>`_detect.diag keeps it).
Without `--details` every message is ONE line, its headline and the
command with the detail (`stage_msg.concise_line`; Essentials first):

```
==> Reading 2 files
Warning: ib_demo.csv: no Cash Report, so its cash is not reconciled: add it to the export
Info: ib_demo.csv: 9 tax objects
==> Processing corporate actions
```

`taxjson run --details` shows each message with its detail lines (the
captured .diag always has them):

```
==> Reading 2 files
Warning: ib_demo.csv: the statement has no Cash Report
Parsed money is NOT reconciled against IB's own totals. Include the Cash Report section in the export (Flex: add it to
the query).

Info: ib_demo.csv: 9 tax objects
==> Processing corporate actions
```

`out.console_lint(text)` checks it (a continuation is valid only
directly under an entry line or another continuation; a blank line
only after an entry of more than one line);
tests/test_run_console.py runs it on the style projects, on stdout and
stderr apart and merged (`2>&1`). The same rule holds for any command's
stderr messages (`tests/_style.assert_console`). The other commands'
progress lines are steps too (`taxjson checklist`: `==> Checking sanity
(taxjson sanity)`).

A message an engine pass says is shown once per run, whichever pass
says it (the account's gains, the blended pass, a failed stage's echo):
keyed on its text, a short position on its symbol and account. A failed
stage's last line repeats its own error headline. stdout and stderr are
line-buffered, so a pipe (`2>&1 | tee`) keeps whole lines in order.

The captured text never changes for this: at width 0 (TAXJSON_WIDTH=0,
a stage's stderr in work/*.diag, the `.sum` DIAGNOSTICS) every message
keeps its bytes. The display form is made at display time —
`out.relabel` (the label, ATTENTION dropped, the topic capitalised),
`stage_msg.reword` (a table of the frequent wordy stage notes, keyed on
their captured text, in a short display form; a line it does not match
is shown as it is), `stage_msg.console_lines` (the flush-left
continuations, the wrap; `stage_msg.is_continuation` tells a captured
continuation line from a new message, so the run's echo prints it with
`out.show(..., cont=True)` and the blank line falls after the whole
message). A reworded message keeps its meaning and every
action the reader must take.

The steps, in run order (an account's steps repeat per account; a step
whose stage is cached under `--fast` is not shown):

| step | when |
| --- | --- |
| `==> Checking the project` | always: the checks before the first stage (config warnings, a $0 election, a leftover file) are under it |
| `==> Rebuilding everything: taxjson's code changed ...` | `--fast` after an upgrade |
| `==> Reading 3 .tt OPENING cost=unknown lines (openings for sales with no purchase in the files)` | an account's .tt files have such lines |
| `==> Loading currency rates` | always |
| `==> Downloading USD → CAD rates` | a rate refresh (`Loading cached USD → CAD rates` with TAXJSON_OFFLINE=1: the cache only) |
| `==> margin  (taxable, first pass: transfers between your accounts)` | accounts read first so transfers (crypto: sends) pair across them — every equity account, sheltered too, when there are two or more (a transfer journal joins two listings in every account's books); the account's books later take this read (same command, same files), so its `Reading` step and messages are shown once, here |
| `==> tfsa  (sheltered)` / `==> margin  (taxable)` / `==> crypto  (taxable, crypto)` | an account's books |
| `Info: File inputs/tfsa/x.csv → identified as Interactive Brokers` | with `--details`: one per input file (a message, not a step; work/`<acct>`_detect.diag keeps it) |
| `==> Reading 2 files` / `==> Reading 1 Kraken file` | the broker parse (the broker named when the account has several) |
| `Info: x.csv: 384 tax objects` | one per file parsed |
| `==> Processing corporate actions` | once per account |
| `==> Reading tfsa_extra.tt` | a .tt file |
| `==> Sorting, de-duplicating, mapping tickers, converting currency` | the books (equity) |
| `==> Applying [[distributions]] from taxjson.toml` | taxable books with distributions |
| `==> Merging the exports`, `==> Sorting, de-duplicating`, `==> Mapping tickers`, `==> Looking up crypto prices`, `==> Converting currency to CAD` | the books (crypto) |
| `==> Writing crypto sends inputs/crypto/crypto_sends.tt` | decided sends booked |
| `==> Calculating capital gains` | the gains |
| `==> Writing holdings reports/tfsa_holdings.toml` | equity accounts (the native-currency holdings stages are not shown) |
| `==> Writing summary reports/tfsa.sum` | every account |
| `==> Combining sheltered accounts` | registered accounts, for the loss checks |
| `==> Checking for missing purchase history` | a position goes short with no purchase in the files: after every account's books, each one that bears on the tax year as a `Warning:` (or one `Info:` per taxable account), the rest in ONE `Info:` line naming `find-missing-history` (`--write-missing-history --outside-year` in its detail, `run --details`) |
| `==> Checking crypto for superficial losses with the sheltered accounts` | one crypto account (US: wash sales) |
| `==> Pooling cost and checking superficial losses across taxable accounts (margin, qt)` | Canada's blended pass (US: `Checking wash sales across ...`; crypto: `... crypto accounts`) |
| `==> Writing summary reports/margin_wash.sum` | the filing-basis summary |
| `==> Writing cross-account reports to reports/` | full runs |
| `==> Writing fees report reports/fees.rpt` | always |
| `==> Checking the filed years` | filed/ locks exist |
| `==> Calculating FX gains on cash (fx_cash_gains = true)` | opted in |
| `==> Done. Reports are in reports/` | the run finished |
| `==> Checking positions against the broker's holdings files` | `holdings` configured (one message per finding; `taxjson sanity` has the tables) |
| `==> Before you trust these numbers (docs/getting-started.md, step 5)` | something is incomplete: a `Warning:` per number the totals miss, an `Info:` per check not made, `Info: Then run taxjson checklist ...` |

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
- the captured form of every message (width 0): `[<prog>: ]note:` /
  `warning:` / `error:`, lower case — the person's `Info:` / `Warning:`
  / `Error:` labels are display only;
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

Pin the layout, not the wording, with `out.lint(text, width_=120,
allow=(...))` (the default width is `out.WIDTH`, the piped width): no
prose line over the width (table rows and lines
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
  per process and runs a command as a pipe would (width 120,
  `out.WIDTH`; `_style.PIPE_WIDTH`):

  ```python
  from _style import project, assert_styled
  r = project("canada").run("harvest", "--no-ibkr", "--options")
  assert_styled(self, r.stdout)              # out.lint at width 120
  assert_console(self, r.stderr)             # out.console_lint
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
