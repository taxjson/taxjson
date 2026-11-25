#!/usr/bin/env python3
"""The A/B byte proof (PLAN step 15): a report's bytes, every way a person
can see them, before and after a change that means to move none of them.

    python3 scripts/style/byte_proof.py --record DIR [--reach-base BASE | --reach-head HEAD] [--argv FILE] [--book B ...]
                                        [--jobs N] [--at ISO ...]
    python3 scripts/style/byte_proof.py --compare A B --trees BASE HEAD
    python3 scripts/style/byte_proof.py --list-commands [--argv FILE] [--book B ...]

--record, in this checkout, for each golden book (every one by default):
at each instant (INSTANTS: the clock stands still, below), a copy of
tests/golden/<book>/project run (`tjs run --no-input` at width 0, as the
golden books run: `project`), then each command in PASSES, each pass on a
fresh copy. On every command: TAXJSON_WIDTH 0 and 40, piped, stdout and
stderr apart and merged (2>&1: the order a person piping both sees);
TAXJSON_WIDTH unset, piped (the default width, 120), apart and merged; on
a terminal (a pty of TTY_ROWS rows, stdin, stdout and stderr on it, so
every isatty branch runs, colour and prompts among them) at TTY_COLUMNS;
and with one stream piped and the other, and stdin, on the terminal
(`outpiped`: `tjs sum > f`; `errpiped`: `tjs sum 2> f`), so a stream
decided by the wrong one shows. On every command that wrote to stdout:
its stdout's reader gone before it starts (`gone`: `tjs sum | true`, the
exit and stderr kept). On every command that printed a byte past ASCII:
PYTHONIOENCODING=ascii, piped (`ascii`: what cannot be shown degrades).
On every command whose bytes move with the width (`width_bound`: its
bytes at width 40 are not its bytes at 0, or the terminal did not show
its bytes at 0): TAXJSON_WIDTH 120, 160, NARROW (30, under out's floor of
40) and NAN_WIDTH (not a number), piped; on a terminal at NARROW (the
floor shows) and WIDE (200, over its cap of 160: the cap shows), and with
COLUMNS set to COLUMNS_SET (72, not the pty's: what reads COLUMNS and what
reads the terminal differ). The width reaches a byte only through
lib/out's `width` and argparse's help, so a command that prints the same
at 0, at 40 and on the terminal prints it at every width; each command's
`passes` file names the passes it ran, so a change that makes it move
with the width is a difference. On every command that colours (COLOURS,
and any whose terminal output held an escape): NO_COLOR set on the
terminal (`nocolor`); on every one that colours or moves with the width,
COLUMNS and NO_COLOR set, piped (`pipedenv`). A prompt reads the line's
stated stdin, then EOF, as after Ctrl-D (QUEUED_EOFS are queued before
the command starts; a command still running after TIMEOUT is refused,
never waited for); on a pipe stdin is /dev/null, so a line that states
its stdin runs every pass too (off a terminal, the refusals of a command
that needs one) and its stated exit is checked where stdin is the
terminal. The run itself is recorded the same way, as `_run`, from the
project before it ran (an engine book, which has no project, as its
compute_gains on both countries, `_ENGINE`). A view the book runs in
another folder (`cwd`: ../2025's `run` and `handoff`) is the command
`FOLDER: ARGV`: it runs on a copy of the project as the book's views
before it left it, run once in order (`view_states`); a command in
another folder that is not a view, on the project as every view left it.

Every child runs in a network namespace of its own (`isolated`: `unshare
--user --map-current-user --net`, so no address is reachable), under a
sitecustomize (`shim_dir`, first on PYTHONPATH, before the clock's) that
refuses each socket connect, send and name lookup, says so on stderr and
appends it to $HOME/.byte-proof-network: a child that tries shows, in its
output and in HOME's files, and reaches nothing. A recording is refused
when the namespace cannot be made (`isolation_problem`); proof.json holds
the isolation, and the recorder itself, run as a script, refuses sockets
too (it imports tests/_golden, which imports taxjson).

The clock stands still: every child reads one instant (tests/_clockshift's
TAXJSON_TEST_CLOCK_AT) in the zone TZ, and every file and folder of a copy
is dated FILE_AGE before it (`set_times`), so a time read from the clock
and one read from a file differ, as a date shown in UTC and one in local
time do (INSTANTS are at 21:30, the next day in UTC). The second instant
is in March, where the January to April branches run. The children's
environment is the tool's own (`child_env`, written to proof.json): a HOME
and a TMPDIR of each run's own (the HOME holds tests/_hermetic's synthetic
rate cache, dated from the instant), LANG, TERM and PATH fixed, NO_COLOR
and COLUMNS only in their passes, TAXJSON_OFFLINE, and the proxies
pointed at a closed local port; nothing is inherited. PYTHONHASHSEED is
left unset (random), as a person runs it: an order that depends on it
shows as a difference.

Recorded under DIR/<book>/<name>/<instant>/ (`command_name`; `_run` the
run; the instant as YYYYMMDDTHHMM): `passes`, the passes run; for each
pass P: P.stdout and
P.stderr (each stream that went to a pipe), P.merged (both on one pipe),
P.out (what the terminal showed), P.meta (the exit codes, then each file,
folder or link added, removed, changed or given another mode anywhere in
the run's folder: the project, the folder above it, HOME and TMPDIR,
`changes`) and P.files/<path>, each file added or changed as bytes but
work/'s stage files (`compared`: every report, export, ledger and .diag
file). Masked (`masked`), and nothing else: each folder the tool made, as
given and as its real path, by its own label (`<project>` the copy, `<top>`
the folder above it, `<home>`, `<tmp>`, `<run>` the run's project, `<tree>`
this checkout, `<shim>` the network shim's, ...), every `<` written as
`<<` first, so a label is never something a command printed; the commit a
build record names (`"commit"` in a `"taxjson"` object, `(commit SHA`
after `taxjson VERSION`) as `<commit:LENGTH>`; and this checkout's package
fingerprint (pipeline/freshness `_package_fingerprint`, the code every
work/ record names: it moves with any byte of src/taxjson, a comment's
too) as `<code-fingerprint>`, that value only. Any other digest, a date, a
time, a count are bytes. The fingerprint reaches no recorded file today
(work/'s records hold it, and their bytes are not recorded); the label
keeps a comment from being a difference if one ever shows it, and a
control (a tree with a comment appended, against its own) shows it.

The commands, without --argv (`default_commands`): each book's views
(book.json), once each; its EXTRA_COMMANDS; its OPTION_LINES; and the
option forms (`option_forms`): from the command registry (cli/parser
`_build_parser`), for each command on each project book, its default
form, `--details` and `--json` where it has them (GEN_ARGS its required
arguments, GEN_BOOKS the books of a command that reads no project,
GEN_SKIP the commands that need a file or a folder beside the project,
with the lines that reach them, and GEN_EXITS the books where they exit
2). Every option of the registry is passed by a recorded line or listed in
REVIEWED_OPTIONS with its reason (tests/test_byte_proof.py fails on one in
neither). The OPTION_LINES and option forms run at the first instant only
(`first_instant_only`): the second instant's branches are the dates',
which the views, EXTRA_COMMANDS and the runs reach at both. --argv FILE adds its
lines to them, never in their stead: one per line, `BOOK COMMAND` (BOOK a
book's name, or `*` for each project book recorded; COMMAND `ARGV...` or
`FOLDER: ARGV...`; a line starting `#` is a comment; ARGV a `tjs`
command, or a stand-alone tool's, `taxjson-sum-gains ...`, run as its
console script), then its notes:
  `# exit N`: the exit it expects, checked in every pass (but `gone`). A
      command that exits 2 (a usage error: argparse's, or a refusal before
      any work) in any pass is refused unless its line says `# exit 2`.
  `# stdin: TEXT`: the answer typed at its prompts (`\\n` a line break),
      then EOF, on the terminal; on a pipe stdin is /dev/null.
  `# edit: OP PATH [TEXT]`: a change to the copy before it runs (EDITS:
      `touch` dates the file an hour before the instant, newer than the
      copy's others; `append` adds TEXT and a line break, the file (made
      with its folders when missing) dated so; `remove` deletes a file or
      a link), PATH from the folder it runs in, inside the copy, through
      no link out of it, and no path in TEXT out of it either (read from
      that folder and from the file's). An edit written after a `#
      before:` is made after the commands before it (`Line.after`).
  `# before: ARGV`: a command run first on the copy, after its edits, piped
      at width 0 and not recorded (it must exit 0); the copy is dated
      again after them (so no `touch` before it). A message that needs
      books built from an edited project: the edits make it, `run` builds
      it; one that needs a file a command wrote changed: `# before:` it,
      then `# edit:` it.
--list-commands says which `tjs` commands and stand-alone tools they
reach, and how many of the registry's options (with a warning for each in
neither them nor REVIEWED_OPTIONS).

What a line may run (`refusal`, before anything runs): nothing that
pushes, installs, changes a release channel or reaches the network
(DENIED, DENIED_FORMS); a `tjs` command REVIEWED, a stand-alone tool of
TOOLS; and no argument or edit that names a path outside the copy: not
absolute, not `~`, not climbing out with `..`, not through a link that
leads out, not a glued `-C` (`-C../..`), each spelling checked (the word,
its parts between `=`, `,` and `:`, a short option's glued value, after a
leading `@`), again against the copy itself before each command runs.

--compare A B --trees BASE HEAD: refuses a recording whose files do not
match its proof.json manifest (each file's size and sha256; a recording
with none), and two recordings of other instants, zones, file ages,
passes, environments, isolation, commands, Pythons, packages (the
children's distributions, name==version), tools (TOOL_FILES' bytes),
checkouts (a clone's against an archive's: `help` and `parser-check` read
the checkout they run from, `checkout_kind`) or TMPDIR lengths (a temp
folder's path is labelled, but its length still moves a wrap or an
archive's size); then byte equality of every file, no parsing and no
tolerance. Then the reach gate (`reach_gate`): BASE and HEAD (each a
folder holding src/taxjson, or a commit of this checkout's git) must be
the trees recorded (their src/taxjson digests, proof.json `src`), each
recording traced against the other tree as its side (A, BASE's, made
with `--reach-head HEAD`; B, HEAD's, with `--reach-base BASE`: each
recorded child traces the files that differ, sys.monitoring LINE and
PY_START events, BRANCH on the changed lines' branches, JUMP and
INSTRUCTION in the loops around them, and the defaults on them, and opens
of the files that are not Python, `_reach` in `shim_dir`, `reach_spec`;
a `# before:` command's are not counted). Every byte the diff changes is
a changed line (`diff_rows`: a line whose code changed, else one whose
bytes did, a comment or a coding cookie, which needs its module
imported), and each needs, on its side (HEAD's added or changed lines in
B, BASE's deleted or replaced lines in A; `file_requirements`): to have
run in each code object on it (a line with no instruction of its own: the
part of its statement on it and each conditional around that part; a
clause line: its body's first statement); each branch on it taken both
ways; in a loop's body, two iterations of one entry of the loop; a
default on it used; a function whose body changed called; in HEAD, each
text on it shown in B's output (`text_requirements`) and a deletion's
neighbour run; each statement the same tries, finallys and withs around
it (`context_requirements`); a file not Python, opened (and in HEAD its
changed lines shown). A line no command can reach may be listed in
HEAD's UNREACHABLE file (`[BASE ]src/taxjson/PATH:LINE :: TEXT ::
REASON`), which excuses that line while it holds TEXT. Exit 0 equal, 1
not (the unreached lines said too), 2 refused, 3 equal but both
recordings of one tree (`checkout`: its commit and its files' digest, or
with no git the digest of every file but the build and cache folders): a
warning, as that shows the proof repeats, not that a change kept every
byte; 4 refused by the reach gate (each changed line not reached as it
needs named, most specific first: a line, then a deletion, then a
function never called, then a module never imported). Equal bytes and a
passing gate show the commit prints the same on the inputs recorded; a
difference only on a value no command makes escapes (CONTRIBUTING.md).

What an equal comparison is, after the fifth soundness review (Fable,
2026-11-20; backlog TJ-4820 to TJ-4826): BASE and HEAD wrote the same
bytes for the commands recorded, on the golden books, at the instants,
widths and terminals of the passes, and nothing past them. The reach gate
does not make it a proof: it asks that each changed line ran, not that
every input prints the same, and the review passed changed bytes through
it in five classes: a block moved into a new function with its text kept,
which `diff_rows` aligns as unchanged (a `return` that now leaves only
the helper); a report held back and printed at its end, so a crash or a
stderr line part-way shows other bytes; a changed binding read only on
lines no command ran; a difference only some data shows (no row, one
item, an empty last column, a longer line, a text under TEXT_MIN, an
exception no input raises); and an UNREACHABLE entry, which waives every
rule of its line. Its patching stopped there. The byte proof is a
regression check (main against step 15's combined tip), not a proof; each
step 15 commit is proved by structure, by AST checks kept with the
step's records: a move by inlining each new function back at its call
(BASE's AST required), a conversion by rewriting each op of a live exact
Doc back to the print it is (BASE's AST required).

A step 15 commit records its parent (`--reach-head HEAD`) and itself
(`--reach-base PARENT`),
each from a clone of its own holding this commit's TOOL_FILES (copied into
the parent's), one recording at a time on a machine under one TMPDIR, and
compares them with `--trees PARENT HEAD` (CONTRIBUTING.md, Running tests).
Every book is synthetic.
"""
import argparse
import ast
import datetime
import difflib
import dis
import functools
import hashlib
import importlib
import io
import json
import os
import platform
import re
import select
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import tokenize
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from types import CodeType, MappingProxyType
from typing import NamedTuple, Optional

# What no recorded process may do (`isolated`, `shim_dir`): connect a
# socket, send to an address, look a name or an address up.
NETWORK_EVENTS = frozenset(("socket.connect", "socket.getaddrinfo", "socket.gethostbyname",
                            "socket.gethostbyaddr", "socket.getnameinfo", "socket.sendto",
                            "socket.sendmsg"))


def _refuse_sockets_here() -> None:
    """The recorder's own process, run as a script, refuses NETWORK_EVENTS
    (it imports tests/_golden, which imports the taxjson under test)."""
    def hook(event, _args):
        if event in NETWORK_EVENTS:
            raise PermissionError(f"byte proof: {event} refused in the recorder")
    sys.addaudithook(hook)


if __name__ == "__main__":
    _refuse_sockets_here()

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "src"), str(REPO / "tests")]
# The golden books, and tests/_hermetic's synthetic rate cache.
_golden = importlib.import_module("_golden")
_hermetic = importlib.import_module("_hermetic")


class Pass(NamedTuple):
    """A way a person sees a command: `width` TAXJSON_WIDTH (None unset);
    `streams` "piped" (stdout and stderr on pipes: apart, then merged),
    "apart" (on pipes apart only), "gone" (stdout a pipe whose reader
    closed before the command started, stderr on a pipe: `tjs sum | true`),
    "tty" (stdin, stdout and stderr on a terminal), "outpiped" (stdout on
    a pipe, stderr and stdin on the terminal) or "errpiped" (stdout and
    stdin on the terminal, stderr on a pipe); `columns` the terminal's
    (None: no terminal); `env` its own variables; `on` the commands it
    runs on (`runs_on`): "every"; "stdout", those that wrote to stdout;
    "ascii", those that printed a byte past ASCII; "width", those whose
    bytes the width moves (`width_bound`); "colour", those that colour
    (`colours`); "width|colour", either."""
    name: str
    width: Optional[str]
    streams: str
    columns: Optional[int]
    env: tuple
    on: str


WIDTHS = (0, 30, 40, 120, 160)
TTY_ROWS, TTY_COLUMNS = 40, 100
# Under lib/out's MIN_WIDTH (40) and over its MAX_WIDTH (160): a terminal
# narrower than the floor and one wider than the cap, each clamped; and
# TAXJSON_WIDTH under the floor (clamped) and not a number (ignored).
NARROW, WIDE = 30, 200
NAN_WIDTH = "wide"
# COLUMNS on a 100-column terminal: what reads COLUMNS (shutil, argparse)
# and what asks the terminal give other widths.
COLUMNS_SET = 72
# In this order: "every" first, then those that ask what they printed
# ("stdout", "ascii", "width"), then those that ask what the terminal
# showed ("width|colour", "colour").
PASSES = (Pass("w0", "0", "piped", None, (), "every"),
          Pass("w40", "40", "piped", None, (), "every"),
          Pass("piped", None, "piped", None, (), "every"),
          Pass("tty", None, "tty", TTY_COLUMNS, (), "every"),
          Pass("outpiped", None, "outpiped", TTY_COLUMNS, (), "every"),
          Pass("errpiped", None, "errpiped", TTY_COLUMNS, (), "every"),
          Pass("gone", None, "gone", None, (), "stdout"),
          Pass("ascii", None, "apart", None, (("PYTHONIOENCODING", "ascii"),), "ascii"),
          Pass("w120", "120", "piped", None, (), "width"),
          Pass("w160", "160", "piped", None, (), "width"),
          Pass("w30", str(NARROW), "apart", None, (), "width"),
          Pass("wnan", NAN_WIDTH, "apart", None, (), "width"),
          Pass("tty30", None, "tty", NARROW, (), "width"),
          Pass("tty200", None, "tty", WIDE, (), "width"),
          Pass("columns", None, "tty", TTY_COLUMNS, (("COLUMNS", str(COLUMNS_SET)),), "width"),
          Pass("pipedenv", None, "apart", None, (("COLUMNS", str(COLUMNS_SET)), ("NO_COLOR", "1")),
               "width|colour"),
          Pass("nocolor", None, "tty", TTY_COLUMNS, (("NO_COLOR", "1"),), "colour"))
# The commands that colour a terminal (found by the escapes and NO_COLOR
# in src/taxjson; tests/test_byte_proof.py fails on a new place): the
# `nocolor` pass runs on them, and on any command whose terminal output
# held an escape.
COLOURS = frozenset(("audit", "taxjson-audit", "taxjson-diff", "taxjson-explain", "taxjson-sum-gains"))
QUEUED_EOFS = 64
TIMEOUT = 900
FORMAT = 6
PROOF = "proof.json"
# The reach gate (`reach_gate`): the code under proof, the sys.monitoring
# tool id the children's trace takes (`shim_dir`), the file of changed
# lines no command can reach (in HEAD), and the gate's exit.
SRC = "src/taxjson"
REACH_TOOL = 3
REACH_FILE = "reach.txt"
UNREACHABLE = "scripts/style/byte_proof_unreachable.txt"
REACH_REFUSED = 4
MAX_SHOWN = 20
RUN = "_run"
# The instants every child reads, in the zone TZ: past (a file a command
# writes is newer than the files dated before them), at 21:30, the next
# day in UTC, and the second in March (the January to April branches).
INSTANTS = ("2026-11-18T21:30:00-05:00", "2026-03-02T21:30:00-05:00")
# A copy's files are dated this long before the instant: a day and a half,
# so a date or a time read from a file is not the clock's.
FILE_AGE = 36 * 3600
# A file an edit touches or appends to is dated this long before the
# instant: newer than the copy's other files, older than the clock.
EDIT_AGE = 3600
EDITS = ("touch", "append", "remove")
TZ = "EST5EDT,M3.2.0,M11.1.0"
PATH = "/usr/local/bin:/usr/bin:/bin"
_CLOSED_PORT = "http://127.0.0.1:9"
# Every child in a network namespace of its own, its user the caller's,
# and killed if the thread that started it dies (`setpriv --pdeathsig`:
# a recording or a test killed leaves no command running).
NETNS = ("--user", "--map-current-user", "--net")
PDEATH = ("--pdeathsig", "KILL")
NETWORK_NOTE = ".byte-proof-network"
# The files that make the recording, not the code it records: a parent's
# clone gets this commit's copies (proof.json's `tool`).
TOOL_FILES = ("scripts/style/byte_proof.py", "tests/_clockshift/sitecustomize.py",
              "tests/_shimchain.py", "tests/_golden.py", "tests/_hermetic/__init__.py")
_CLOCK = REPO / "tests" / "_clockshift"
# Not part of a tree without git: what a build or a run of the tools leaves.
_NOT_TRACKED = {".git", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache",
                "venv", ".venv", "build", "dist", "node_modules"}

# What a command line may run (TJ-4495, TJ-4579): the proof never pushes,
# installs, changes a release channel or reaches the network, whatever an
# --argv file names. DENIED: each `tjs` command that runs git to change a
# repository or reach a remote, pip, uv or an installer, writes outside the
# pass's own folder (the copy, the folder above it, HOME, TMPDIR), or opens
# a socket even with TAXJSON_OFFLINE set; DENIED_FORMS: an option that does
# (and a prefix argparse would take for it). Found by walking the command
# registry (cli/parser `_build_parser`) and each place src/taxjson runs
# git, pip or an installer or opens a connection (urllib, yfinance, IBKR):
# the others are REVIEWED (git read only, to name the build or a project's
# `git status`; prices and rates fetched only without TAXJSON_OFFLINE,
# which every child has, and in a namespace with no network). A
# stand-alone tool runs only if it is one of TOOLS. A line is refused
# (exit 2) before anything runs (`refusal`); tests/test_byte_proof.py fails
# on a registry command in neither set and on a new such place.
# parser-check runs on a fixture folder, not a project: recorded on one
# made in the copy (`--fixtures`); its --update-golden (which writes a
# checkout's fixtures) denied.
DENIED = MappingProxyType({
    "channels": "a `git fetch` of the install's remote (lib/channels)",
    "deploy": "installs a release into this machine's production copy (install.sh, git)",
    "fetch": "downloads statements from a broker through the fetcher plugins",
    "promote": "commits, lands and pushes channels.json (scripts/promote.sh)",
})
DENIED_FORMS = MappingProxyType({
    ("tips", "--online"): "probes Yahoo Finance",
    ("parser-check", "--update-golden"): "writes this checkout's fixture goldens",
})
REVIEWED = frozenset((
    "align", "amt", "audit", "buy-check", "carryover", "cash", "ccd-sum", "check-dates", "check-filed",
    "check-tt", "checklist", "clean", "close-year", "compare", "crypto-sends", "dil", "dil-sum",
    "div-estimate", "divs", "divs-sum", "edge-cases", "elect", "estimate", "events", "fees", "fees-sum",
    "find-missing-history", "form-export", "format", "format-map", "fx-cash", "gains", "gains-base",
    "gains-cra", "handoff",
    "harvest", "help", "init", "instalments", "journals", "leaps", "leaps-sum", "list", "losers",
    "migrate", "new-year", "opening", "option-boundary", "option-yield", "parser-check", "plot-tax",
    "rebuy", "reconcile-slips", "redact", "renames", "roc", "roc-sum", "run", "sanity", "sell-check",
    "shares", "slip-audit", "snapshot", "spinoffs", "splits", "staking", "staking-sum", "stats", "sum",
    "t1135", "tax-logic", "technicals", "ticker-map", "tips", "trades", "trades-sum", "transfers",
    "unclose-year",
    "update-tobase-map", "wash-radar", "wash-sales", "watch", "win-rates", "winners", "years"))
TOOLS = frozenset(("taxjson-audit", "taxjson-diff", "taxjson-explain", "taxjson-fees-sum",
                   "taxjson-sum-gains"))
_ELECT_EVENT = "20240603-parnt-spnco-3509"
# An input of the demo book (from its 2024 folder) and the run's record of
# what it read: what the edits below change, so each refusal of books
# older than their inputs shows.
_DEMO_INPUT = "../inputs/margin/ib_scenarios_demo.csv"
_FINGERPRINT = "work/.inputs_fingerprint.json"

# A project made in the copy (`# edit: append`) and built first (`#
# before:`): one year, 2023, its loss carried back to 2022 (`[carryover]
# claimed`) past a 2021 sale of unknown cost, so close-year's yes accepts
# a carry-back split that leaves a sale out (cli/file
# `_stop_on_incomplete_split`, "answered yes"; no golden book has one;
# tests/rules/ca/carry/test_carry_back_ledger.py `TestAnAcceptedSplit`).
_CARRY_TOML = ('[settings]', 'year = 2023', 'country = "canada"', 'base_currency = "CAD"',
               'source_currencies = []', 'tax_date = "settle"', 'province = "ON"', 'inputs_dir = "../inputs"',
               '[accounts.margin]', 'type = "taxable"', '[estimate]', 'other_income = 50000', '[carryover]',
               'claimed = { 2022 = 40.00 }')
_CARRY_TRADES = ("BUYSELL 2022-02-01 09:30:00 ZZQA.TO 50 CAD 10.00 -500.00 0.00",
                 "BUYSELL 2022-08-01 09:30:00 ZZQA.TO -50 CAD 12.00 600.00 0.00",
                 "BUYSELL 2023-02-01 09:30:00 ZZQB.TO 50 CAD 10.00 -500.00 0.00",
                 "BUYSELL 2023-08-01 09:30:00 ZZQB.TO -50 CAD 8.00 400.00 0.00",
                 'OPENING 2020-12-31 ZZQE.TO 50 cost=unknown reason="synthetic"',
                 "BUYSELL 2021-03-01 09:30:00 ZZQE.TO -50 CAD 2.00 100.00 0.00")
_CARRY = ("-C carry/2023 close-year"
          + "".join(f" # edit: append carry/2023/taxjson.toml {t}" for t in _CARRY_TOML)
          + "".join(f" # edit: append carry/inputs/margin/trades.tt {t}" for t in _CARRY_TRADES)
          + " # before: -C carry/2023 run --no-input")

# A one-folder project made in the copy with its last full run's record
# (work/.inputs_fingerprint.json) written whole by `# edit: append`: the
# record names the inputs as they are and other code, so a command that
# reads the books refuses them as built by another taxjson (cli/common
# `_books_older_than_inputs`; TJ-4310). Its files table is what
# lib/checklist `input_fingerprint` makes of the two files
# (tests/test_byte_proof.py holds it to taxjson's own).
_OTHER_TOML = ('[settings]', 'year = 2024', 'country = "canada"', 'base_currency = "CAD"',
               'source_currencies = []', 'tax_date = "settle"', '[accounts.margin]', 'type = "taxable"')
_OTHER_TRADES = ("BUYSELL 2024-02-01 09:30:00 ZZQD.TO 50 CAD 10.00 -500.00 0.00",)


def _appended(lines) -> bytes:
    """A file's bytes as `# edit: append` makes it of these lines."""
    return "".join(f"{t}\n" for t in lines).encode("utf-8")


def other_taxjson_record() -> str:
    """The record, on one line: `code` another taxjson's, the files table
    the two files' (`taxjson.toml` by its parsed settings, as
    lib/checklist `_run_config_digest` reads it; the trades by their
    bytes)."""
    import tomllib
    settings = tomllib.loads(_appended(_OTHER_TOML).decode("utf-8"))
    files = {"inputs/margin/trades.tt": hashlib.sha256(_appended(_OTHER_TRADES)).hexdigest(),
             "taxjson.toml": "run-settings:" + hashlib.sha256(
                 json.dumps(settings, sort_keys=True, default=str).encode("utf-8")).hexdigest()}
    return json.dumps({"code": "another taxjson", "country": "canada", "files": files, "version": 2},
                      sort_keys=True)


_OTHER = ("".join(f" # edit: append other/taxjson.toml {t}" for t in _OTHER_TOML)
          + "".join(f" # edit: append other/inputs/margin/trades.tt {t}" for t in _OTHER_TRADES)
          + f" # edit: append other/work/.inputs_fingerprint.json {other_taxjson_record()}")
# A ticker.map line a stage's .diag suggests (lib/ticker_map_suggest
# `from_diag`): what `ticker-map --suggest` lists and `--write` appends,
# asks for on a terminal and refuses off one; no golden book's run
# suggests one.
_SUGGESTED = " # edit: append work/zz_suggest.diag warning: ZZQA looks renamed: add to ticker.map: GLOBAL ZZQA ZZQB"
# A broker's positions report (lib/positions_reports: a `[[holding]]`
# TOML), for `opening`: no golden book holds one.
_POSITIONS = "".join(f" # edit: append pos.toml {t}" for t in (
    "[meta]", 'as_of = "2024-06-28"', "[[holding]]", 'symbol = "ZZQC.TO"', "quantity = 10",
    'currency = "CAD"', "total_cost = 100.00"))
# A snapshot saved first, so `compare` renders (lib/compare): no
# difference, one after a report is changed by hand, and one after an
# account's books are removed (the reports match, a figure moved).
_SAVED = " # before: compare --save before"
_CHANGED = " # edit: append reports/margin.sum changed by hand"
_UNBOOKED = " # edit: remove work/crypto_gains.json # edit: remove work/crypto_gains_wash.json"

# Recorded by default after the views (with the option forms): the
# commands no golden view runs. Commit 1's review (TJ-4490): the
# elections' text forms (the list, the pending list, a saved one, two
# refusals) and `--pending --json`; the CRA slip import of a folder with no
# PDF (a book holds no CRA PDF); the wash radar's not-applicable answer (a
# US project's crypto account). Fable's review of 1c (TJ-4495): every other
# `tjs` command that runs offline, unattended and changes nothing outside
# the copy, `run` and `close-year` without --no-input and --yes (their
# terminal branches), the two ../2025 aligns on the books as the views
# leave them, `init` (its January to April hint), five refusals (exit 2:
# `ticker-map` with nothing to do, a file that is not there, `align` from a
# year with no project); and the stand-alone tools docs/commands.md names
# that colour a terminal (`console_scripts`: taxjson-sum-gains, -explain
# --color, -diff, -audit; and -fees-sum), on the demo book's work files.
# Fable's review of 1d (TJ-4579): each prompt answered yes and no on the
# terminal (`# stdin:`: close-year's lock, an election chosen in `elect
# --redo` and in a run whose election is undecided, a crypto send decided
# in a run, align --write's settings, the checklist's walk, close-year's
# lock over a carry-back split that leaves a sale out), and each
# message that needs an altered project (`# edit:`): the refusal of books
# older than their inputs (t1135, sum, estimate, close-year, check-filed,
# form-export, cash, find-missing-history's writes, and the banner of the
# commands that only warn), the run's record removed and unreadable, a
# file's time alone moved (no change). Fable's review of 1e (TJ-4624):
# `compare` against a snapshot saved first, as it was and after a report
# is changed by hand; the refusal of books another taxjson built; a
# ticker.map line suggested, written on the terminal's yes and refused
# off a terminal. Left out:
# `fetch` (a broker's servers), `channels`, `deploy` and `promote` (the
# release channels: git remotes, an install).
EXTRA_COMMANDS = (
    ("demo", "elect"),
    ("demo", "elect --pending"),
    ("demo", "elect --pending --json"),
    ("demo", "elect margin"),
    ("demo", f"elect margin --set {_ELECT_EVENT}=taxable_deemed_dividend"),
    ("demo", f"elect margin --set {_ELECT_EVENT}=taxable_deemed_dividend --hint fmv_per_share=0 # exit 2"),
    ("demo", f"elect --set {_ELECT_EVENT}=taxable_deemed_dividend # exit 2"),
    ("demo", "slip-audit --import-cra ."),
    ("demo", "slip-audit --import-cra . --json"),
    ("style-usa", "wash-radar crypto --date 2024-11-25"),
    ("style-usa", "wash-radar crypto --date 2024-11-25 --json"),
    ("demo", "run"),
    ("demo", "close-year"),
    ("demo", "years"),
    ("demo", "years --json"),
    ("demo", "years --details"),
    ("demo", "checklist"),
    ("demo", "checklist --run"),
    ("demo", "div-estimate --date 2024-12-31"),
    ("demo", "div-estimate --date 2024-12-31 --json"),
    ("demo", "win-rates"),
    ("demo", "win-rates --json"),
    ("demo", "losers"),
    ("demo", "losers --json"),
    ("demo", "buy-check AAPL"),
    ("demo", "buy-check AAPL --json"),
    ("demo", "sell-check NVDA"),
    ("demo", "sell-check NVDA --json"),
    ("demo", "harvest --no-ibkr"),
    ("demo", "harvest --no-ibkr --json"),
    ("demo", "watch"),
    ("demo", "watch --json"),
    ("demo", "divs 2024"),
    ("demo", "divs 2024 --json"),
    ("demo", "fees 2024"),
    ("demo", "trades 2024"),
    ("demo", "trades 2024 --json"),
    ("demo", "gains 2024"),
    ("demo", "gains 2024 --json"),
    ("demo", "leaps"),
    ("demo", "splits"),
    ("demo", "splits --json"),
    ("demo", "renames"),
    ("demo", "renames --json"),
    ("demo", "journals"),
    ("demo", "journals --pending"),
    ("demo", "compare --list"),
    ("demo", "compare --save before"),
    ("demo", "snapshot"),
    ("demo", "snapshot --json"),
    # `tjs clean` removes only inside the copy (lib/clean); its default
    # form, --details and --json are option forms on every project book.
    ("demo", "clean --dry-run"),
    ("demo", "check-dates"),
    ("demo", "check-dates --json"),
    ("demo", "check-tt margin"),
    ("demo", "crypto-sends"),
    ("demo", "crypto-sends --json"),
    ("demo", "format"),
    ("demo", "format --check"),
    ("demo", "format-map"),
    ("demo", "format-map --check"),
    ("demo", "ticker-map # exit 2"),
    ("demo", "ticker-map --suggest"),
    ("demo", "ticker-map --json # exit 2"),
    ("demo", "update-tobase-map"),
    ("demo", "update-tobase-map --json"),
    ("demo", "init --country canada fresh"),
    ("demo", "migrate --dry-run"),
    ("demo", "redact"),
    ("demo", "redact --check"),
    ("demo", "tax-logic"),
    ("demo", "tax-logic --ids"),
    ("demo", "tax-logic --country us --json"),
    ("demo", "plot-tax"),
    ("demo", "plot-tax --json"),
    ("demo", "help"),
    ("demo", "help sum"),
    ("demo", "help --all"),
    ("demo", "reconcile-slips missing.csv # exit 2"),
    ("demo", "opening margin missing.csv --dry-run # exit 2"),
    ("demo", "../2025: align --from 2024"),
    ("demo", "../2025: align --from 2024 --json"),
    ("evidence", "dil"),
    ("evidence", "dil --json"),
    ("evidence", "roc"),
    ("evidence", "roc --json"),
    ("evidence", "align --from 2023 # exit 2"),
    ("style-usa", "staking"),
    ("style-usa", "staking --json"),
    ("style-usa", "staking-sum"),
    ("demo", "taxjson-sum-gains work/margin_gains.json"),
    ("demo", "taxjson-sum-gains work/margin_gains.json --json"),
    ("demo", "taxjson-fees-sum --cache work --year 2024 --to CAD --rates work/to_base.csv"),
    ("demo", "taxjson-explain --country canada --color work/margin_base.json"),
    ("demo", "taxjson-diff work/margin_gains.json work/margin_gains_wash.json"),
    ("demo", "taxjson-audit --country canada --base work/margin_base.json --year 2024"),
    # TJ-4579: each prompt answered yes and no, on the terminal
    ("demo", "close-year # stdin: y"),
    ("demo", "close-year # stdin: n"),
    ("demo", "elect margin --redo # stdin: 1\\n\\nchosen at the prompt"),
    ("demo", "elect margin --redo # stdin: 9"),
    ("demo", "run # stdin: 1\\n\\nchosen in the run # edit: remove ../inputs/margin/manifest.json"),
    ("demo", "run # edit: remove ../inputs/margin/manifest.json"),
    ("crypto", "run # stdin: s # edit: remove inputs/crypto/sends.json"),
    ("crypto", "run # stdin: k # edit: remove inputs/crypto/sends.json"),
    ("demo", "../2025: align --from 2024 --write # stdin: y\\ny\\ny\\ny\\ny\\ny\\ny\\ny"),
    ("demo", "../2025: align --from 2024 --write # stdin: n\\nn\\nn\\nn\\nn\\nn\\nn\\nn"),
    ("demo", "../2025: align --from 2024 --write # stdin: q"),
    ("demo", "checklist --walk # stdin: d\\nwalked"),
    ("demo", "checklist --walk # stdin: q"),
    ("demo", _CARRY + " # stdin: y"),
    ("demo", _CARRY + " # stdin: n"),
    # TJ-4579: the messages of an altered project
    ("demo", f"t1135 # edit: append {_DEMO_INPUT}"),
    ("demo", f"sum # edit: append {_DEMO_INPUT}"),
    ("demo", f"estimate # edit: append {_DEMO_INPUT}"),
    ("demo", f"close-year # edit: append {_DEMO_INPUT}"),
    ("demo", f"close-year --yes # edit: append {_DEMO_INPUT}"),
    ("demo", f"check-filed # edit: append {_DEMO_INPUT}"),
    ("demo", f"form-export # edit: append {_DEMO_INPUT}"),
    ("demo", f"cash all # edit: append {_DEMO_INPUT}"),
    ("demo", f"find-missing-history --write-purchases # edit: append {_DEMO_INPUT}"),
    ("demo", f"divs-sum # edit: append {_DEMO_INPUT}"),
    ("demo", f"sum # edit: remove {_DEMO_INPUT}"),
    ("demo", f"sum # edit: touch {_DEMO_INPUT}"),
    ("demo", f"sum # edit: remove {_FINGERPRINT}"),
    ("demo", f"sum # edit: append {_FINGERPRINT} not json"),
    # TJ-4624: compare's report, a refusal no book reaches, a suggestion written
    ("demo", "compare before" + _SAVED),
    ("demo", "compare before" + _SAVED + _CHANGED),
    ("demo", "compare before" + _SAVED + _UNBOOKED),
    ("demo", "-C other cash # exit 1" + _OTHER),
    ("demo", "ticker-map --suggest --write # stdin: y" + _SUGGESTED),
    ("demo", "ticker-map --suggest --write # stdin: n" + _SUGGESTED),
)

# Recorded by default at the first instant, after EXTRA_COMMANDS: the
# registry's options no option form passes (each command's default form,
# --details, --json), each on a book where it does something; the rest
# are REVIEWED_OPTIONS, each with its reason.
_SENDS_ID = "cb-20241015T110000-ETH-0.5"
_SLIPS = ("slips.csv # edit: append slips.csv symbol,quantity,proceeds,cost"
          " # edit: append slips.csv SPNCO.US,25,1000.00,900.00")
_TT = "../exports/extra.tt # edit: append ../exports/extra.tt BUYSELL 2024-01-10 09:30:00 XQZ 10 USD 40.00 400.00 0.00"
_FILED = ("filed.csv # edit: append filed.csv symbol,date,qty,proceeds,cost,gain"
          " # edit: append filed.csv SAMPG.US,2024-03-18,10,1000.00,900.00,100.00")
_MONEY = "--other-losses 100 --deductions 200 --carrying-charges 50"
# A coinbase fixture folder made in the copy: parser-check reads no
# project, and `parser-check all` reads the fixtures of the checkout it
# runs from, outside the copy (a clone's; an archive has none to find).
_FIXTURE = ("# edit: append fx/sample.csv Timestamp,Transaction Type,Asset,Quantity Transacted,Price Currency,"
            "Price at Transaction,Fees and/or Spread,Total (inclusive of fees and/or spread),Subtotal,Notes,ID"
            " # edit: append fx/sample.csv 2024-01-15 10:00:00 UTC,Buy,ETH,1.5,USD,3000,10,4510,,,")
_EXPECTED = ('[{"account": "Coinbase", "action": "BUYSELL", "currency": "USD", "date": "2024-01-15", '
             '"date_settle": "2024-01-15", "fee": 10.0, "gross_amount": 4500.0, "net_amount": 4510.0, '
             '"price": 3000.0, "quantity": 1.5, "symbol": "ETH", "time": "05:00:00"}]')
OPTION_LINES = (
    ("demo", "../2025: align --from 2024 --write --all"),
    ("demo", f"amt --other-income 50000 {_MONEY}"),
    ("demo", f"estimate {_MONEY} --federal-only"),
    ("demo", f"plot-tax --other-income 50000 {_MONEY} --province BC"),
    ("demo", f"sum {_MONEY} --province AB"),
    ("style-usa", "amt --long-term-losses 100"),
    ("style-usa", "estimate --long-term-losses 100"),
    ("style-usa", "plot-tax --long-term-losses 100"),
    ("style-usa", "sum --long-term-losses 100"),
    ("demo", "audit --summary"),
    ("demo", "audit --all-years --no-trace"),
    ("demo", "audit --no-color"),
    ("demo", "audit --year 2024 --account margin --date 2024-08-05"),
    ("demo", "audit --id f64001ab70"),
    ("demo", "carryover --on-books"),
    ("demo", "carryover --filed-net 100"),
    ("demo", "check-dates --all"),
    ("demo", f"check-tt --equity {_TT}"),
    ("demo", f"check-tt --crypto {_TT}"),
    ("demo", f"check-tt --account margin {_TT}"),
    ("demo", "checklist --section 3"),
    ("demo", "checklist --only sum"),
    ("demo", "checklist --done sum --note checked --show"),
    ("demo", "checklist --done sum --force"),
    ("demo", "checklist --undo sum"),
    ("demo", "checklist --reset"),
    ("demo", "checklist --all"),
    ("demo", "checklist --skip tips # exit 2"),
    ("demo", "checklist --run --quick --jobs 1"),
    ("demo", "close-year --yes --year 2024"),
    ("demo", "close-year --yes --force"),
    ("demo", "close-year --yes --filed-dispositions missing.csv # exit 2"),
    ("demo", f"close-year --yes --filed-dispositions {_FILED}"),
    ("demo", "compare --delete before # exit 2"),
    ("crypto", f"crypto-sends --set {_SENDS_ID}=self --note moved"),
    ("crypto", f"crypto-sends --set {_SENDS_ID}=payment --price 2000"),
    ("crypto", f"crypto-sends --set {_SENDS_ID}=gift --unpair"),
    ("crypto", f"crypto-sends --unset {_SENDS_ID}"),
    ("crypto", "crypto-sends --write"),
    ("demo", "div-estimate --months 6"),
    ("demo", "edge-cases --margin 5"),
    ("demo", "elect margin --reset"),
    ("demo", f"elect margin --reset --event {_ELECT_EVENT}"),
    ("demo", "fees-sum --by-account"),
    ("demo", "fees-sum --no-by-account"),
    ("demo", "find-missing-history --include-options --year 2024"),
    ("demo", "find-missing-history --write-missing-history --all-history"),
    ("demo", "find-missing-history --write-missing-history --outside-year --force"),
    ("demo", "find-missing-history --gen-phantoms x.tt # exit 2"),
    ("style-usa", "form-export --form txf --box B"),
    ("style-usa", "form-export --form txf --out out.txf"),
    ("demo", "format --write"),
    ("demo", "format --write --no-backup"),
    ("demo", "format-map --write"),
    ("demo", "format-map --write --no-backup"),
    ("demo", "update-tobase-map --write"),
    ("demo", "update-tobase-map --write --no-backup"),
    ("options", "gains --options"),
    ("options", "gains --equities"),
    ("options", "gains --futures"),
    ("options", "gains --calls"),
    ("options", "gains --puts"),
    ("options", "gains-base --options"),
    ("options", "gains-base --equities"),
    ("options", "gains-base --futures"),
    ("options", "gains-base --calls"),
    ("options", "gains-base --puts"),
    ("demo", "gains-cra --csv rows.csv"),
    ("options", "trades --options"),
    ("options", "trades --equities"),
    ("options", "trades --futures"),
    ("options", "trades --calls"),
    ("options", "trades --puts"),
    ("demo", "handoff --prior missing.json # exit 2"),
    ("demo", "harvest --no-ibkr --crypto --options --verbose"),
    ("demo", "init --demo demo2"),
    ("demo", "init --country canada --year 2023 --single fresh2"),
    ("demo", "init --single --force --country canada"),
    ("demo", "init --country canada --force"),
    ("demo", "journals --account margin --year 2024 --pending"),
    ("demo", "list --date 2024-06-30"),
    ("demo", "list --negative"),
    ("demo", "losers --top 3"),
    ("demo", "winners --top 3"),
    ("demo", "migrate --to-years # exit 2"),
    ("style-canada", "migrate --to-years"),
    ("style-canada", "migrate --to-years --write"),
    ("demo", f"opening margin {_DEMO_INPUT} --dry-run # exit 2"),
    ("demo", f"reconcile-slips {_SLIPS}"),
    ("demo", f"reconcile-slips --details {_SLIPS}"),
    ("demo", f"reconcile-slips --json {_SLIPS}"),
    ("demo", f"reconcile-slips --tolerance 5 {_SLIPS}"),
    ("demo", "redact --also margin --no-denylist"),
    ("demo", "redact --force"),
    ("demo", f"redact --out red {_DEMO_INPUT}"),
    ("demo", "renames --pending"),
    ("demo", "run --no-input --account margin"),
    ("demo", "run --no-input --strict"),
    ("demo", "sanity --tolerance 0.01 --cost-tolerance 5"),
    ("demo", "shares --options --taxable"),
    ("demo", "shares --sheltered --sort qty"),
    ("demo", "slip-audit --tolerance 5"),
    ("demo", "slip-audit --template"),
    ("demo", "slip-audit --import-cra . --write"),
    ("style-usa", "staking --broker coinbase --symbol ETH"),
    ("style-usa", "staking-sum --broker coinbase --symbol ETH"),
    ("demo", "stats --all-history"),
    ("demo", "win-rates --all-history --by month"),
    ("demo", "win-rates --by account"),
    ("demo", "win-rates --by class"),
    ("style-usa", "wash-radar crypto --date 2024-11-25 --verbose"),
    ("demo", "wash-sales --explain"),
    ("demo", "watch --threshold 50 --state work/w.json --exit-code"),
    ("demo", "watch --harvest --no-ibkr"),
    ("demo", "../2025: years --diff 2024 2025"),
    ("demo", f"parser-check coinbase --fixtures fx {_FIXTURE} # edit: append fx/expected.json []"),
    ("demo", f"parser-check coinbase --fixtures fx {_FIXTURE} # edit: append fx/expected.json {_EXPECTED}"),
    # TJ-4624: each option passed on a line that does not refuse (exit 2)
    ("demo", "compare before --details" + _SAVED + _CHANGED),
    ("demo", "compare before --json" + _SAVED + _CHANGED),
    ("demo", "compare --delete before" + _SAVED),
    ("demo", "compare --delete before --json" + _SAVED),
    ("demo", f"elect margin --set {_ELECT_EVENT}=rollover_s_86_1 --hint allocated_acb_cad=100"),
    ("demo", "../2025: handoff --details"),
    ("demo", "../2025: handoff --prior ../2024/filed/2024.json"),
    ("demo", "opening margin pos.toml" + _POSITIONS),
    ("demo", "opening margin pos.toml --dry-run" + _POSITIONS),
    ("demo", "opening margin pos.toml --details --dry-run" + _POSITIONS),
    ("demo", "opening margin pos.toml --date 2024-06-30 --dry-run" + _POSITIONS),
    ("demo", "opening margin pos.toml --force" + _POSITIONS),
    ("demo", "ticker-map --suggest" + _SUGGESTED),
    ("demo", "ticker-map --suggest --details" + _SUGGESTED),
    ("demo", "ticker-map --suggest --json" + _SUGGESTED),
    ("demo", "ticker-map --suggest --write --all" + _SUGGESTED),
    ("demo", "unclose-year --dry-run # before: close-year --yes"),
    ("demo", "unclose-year --year 2024 # before: close-year --yes"),
)
# The registry's options no recorded line passes, each with its reason
# (tests/test_byte_proof.py fails on an option in neither, and on an entry
# here that a line passes or the registry no longer has).
_DENIED_CMD = "a DENIED command"
REVIEWED_OPTIONS = MappingProxyType({
    ("channels", "--offline"): _DENIED_CMD + " (git remotes, the release channels)",
    ("channels", "--json"): _DENIED_CMD + " (git remotes, the release channels)",
    ("fetch", "--list"): _DENIED_CMD + " (a broker's servers)",
    ("fetch", "--fetcher"): _DENIED_CMD + " (a broker's servers)",
    ("fetch", "--json"): _DENIED_CMD + " (a broker's servers)",
    ("fetch", "--dry-run"): _DENIED_CMD + " (a broker's servers)",
    ("tips", "--online"): "a DENIED_FORMS form (Yahoo Finance)",
    ("parser-check", "--update-golden"): "a DENIED_FORMS form (it writes the checkout's tests/fixtures)",
    ("harvest", "--ibkr-port"): "a TWS or IB Gateway socket: the namespace refuses it; `--no-ibkr` is recorded",
    ("technicals", "--benchmark"): "no golden book holds a history cache, and the proof runs offline: "
                                   "every form stops before a statistic (no history: exit 1, the "
                                   "default form records it; a bare-word symbol refused first, exit "
                                   "2); tests/features/totals/test_technicals_command.py runs "
                                   "--benchmark on a made-up cache",
    ("checklist", "--skip"): "retired (`--done --force`): it always refuses, exit 2 (recorded)",
    ("find-missing-history", "--gen-phantoms"): "retired (--write-missing-history): it always refuses, "
                                                "exit 2 (recorded)",
})

# The option forms (`option_forms`): each registry command's default form,
# `--details` and `--json` where it has them, on each project book. GEN_SKIP:
# the commands not generated (their reason: the lines that reach them);
# GEN_ARGS: a command's required arguments (by book, or for every book);
# GEN_BOOKS: the books of a command that reads no project; GEN_NOT: a form
# recorded already under another line (the run's default form is `_run`);
# GEN_EXITS: the books on which a command's forms exit 2 (a refusal
# before any work).
GEN_SKIP = MappingProxyType({
    "align": "needs a year project beside it: EXTRA_COMMANDS' ../2025 aligns and evidence's refusal",
    "opening": "needs a broker's positions report: OPTION_LINES make one (`# edit: append`)",
    "reconcile-slips": "needs a slip CSV: OPTION_LINES make one (`# edit: append`)",
    "parser-check": "reads a fixture folder, not a project: OPTION_LINES make one (`# edit: append`)",
})
_SYMBOL = {"demo": "AAPL", "style-canada": "SPNC.US", "style-usa": "SPNC.US", "evidence": "XQZ.TO",
           "crypto": "BTC", "options": "XQZ.TO"}
GEN_ARGS = MappingProxyType({
    "buy-check": _SYMBOL, "sell-check": _SYMBOL, "harvest": "--no-ibkr", "new-year": "2025",
    "init": "--country canada fresh", "run": "--no-input",
})
GEN_BOOKS = MappingProxyType({"help": ("demo",), "init": ("demo",), "tax-logic": ("demo", "style-usa"),
                             "run": ("demo",)})
GEN_NOT = frozenset((("run", "run --no-input"),))
_PROJECTS = ("crypto", "demo", "evidence", "options", "style-canada", "style-usa")
GEN_EXITS = MappingProxyType({
    # no snapshot saved yet (`compare --save`), no year before it (handoff)
    "compare": _PROJECTS, "handoff": _PROJECTS,
    # no ticker.map line to suggest; a project not in a year folder; no format map
    "ticker-map": _PROJECTS, "new-year": ("crypto", "evidence", "options", "style-canada", "style-usa"),
    "format-map": ("crypto", "options", "style-canada", "style-usa"),
})

# The checkout's commit in the build record a lock or a snapshot keeps
# (lib/channels `running_build`, `build_label`): masked, its length kept.
_BUILD = (
    re.compile(r'("taxjson":\s*\{[^{}]*?"commit":\s*")([0-9a-f]{7,40})(")'),
    re.compile(r"(\btaxjson \S+ \(commit )([0-9a-f]{7,40})()\b"),
)
# A line's notes, after its command: `# exit N`, `# stdin: TEXT`, `# edit: OP PATH [TEXT]`.
_NOTES = re.compile(r"\s+#\s*(?=(?:exit|stdin|edit|before)\b)")
_EXIT = re.compile(r"exit\s+(\d+)\s*")
_STDIN = re.compile(r"stdin:[ \t]?(.*?)\s*")
_EDIT = re.compile(r"edit:\s*(\S+)\s+(\S+)(?:[ \t]+(.*?))?\s*")
_BEFORE = re.compile(r"before:\s*(\S.*?)\s*")
_GLOBAL_WITH_VALUE = ("-C", "--dir")
NAME_MAX = 120

# An engine book's run (tests/_golden.py `_run_engine`), in a child: each
# country's compute_gains on its inputs.json, the result written beside it.
ENGINE_RUN = "<engine>"
_ENGINE = """
import json
from pathlib import Path
from taxjson.engine.by_country import get_tax_rules
from taxjson.lib.pipeline import prepare_books
from taxjson.model.transaction import TaxTransaction
m = json.loads(Path("book.json").read_text(encoding="utf-8"))
doc = json.loads(Path("inputs.json").read_text(encoding="utf-8"))
for country in ("canada", "usa"):
    kw = {"per_account_basis": True} if country == "usa" else {}
    txs = [TaxTransaction(**r) for r in doc["taxable"]]
    shel = [TaxTransaction(**r) for r in doc["sheltered"]]
    if m.get("prepare"):
        txs, shel, _accounts, _log = prepare_books(txs, shel, taxable=True, phantom_hint=False)
    result = get_tax_rules(country).compute_gains(txs, sheltered_transactions=shel, **kw)
    Path(f"{country}.json").write_text(
        json.dumps(result, indent=1, sort_keys=True, default=str) + "\\n", encoding="utf-8")
"""

# The network shim every child loads first (`shim_dir`): NETWORK_EVENTS
# refused, said and noted, before the next sitecustomize (the clock's).
_SHIM = '''"""The byte proof's network shim (scripts/style/byte_proof.py `shim_dir`):
first on every child's PYTHONPATH, in a network namespace of its own. A
socket connect, a send to an address and a name lookup are refused, said
on stderr and appended to $HOME/<<NOTE>>; the reach gate's trace is set
up (`_reach`); then the next sitecustomize loads (tests/_shimchain.py)."""
import os
import sys

_EVENTS = frozenset(<<EVENTS>>)


def _refuse(event, args):
    if event not in _EVENTS:
        return
    shown = tuple(a for a in args if type(a).__name__ != "socket")
    what = "byte proof: %s refused: %r" % (event, shown)
    try:
        os.write(2, (what + "\\n").encode("utf-8", "backslashreplace"))
    except OSError:
        pass
    try:
        with open(os.path.join(os.environ.get("HOME") or ".", "<<NOTE>>"), "a", encoding="utf-8") as f:
            f.write(what + "\\n")
    except OSError:
        pass
    raise PermissionError(what)


sys.addaudithook(_refuse)


def _reach():
    """The reach gate's trace (byte_proof `reach_begin`, `reach_spec`):
    when <<REACH>> beside this file names a folder and what to trace, each
    code object loaded from a traced Python file gets sys.monitoring
    events, each code object known by its place in its module's code
    tree: LINE and PY_START (each seen once: the lines that ran, by code
    object, and the code objects entered); BRANCH at the watched branches
    (each until it went both ways; a line event at a watched instruction
    is kept until then, as disabling it skips that instruction's events
    once); INSTRUCTION and JUMP where a changed
    line in a loop is probed (until it ran in two iterations of one entry
    of its loop: a back-edge of that loop between, none of a loop around
    it, in one frame); and a watched default's argument at PY_START
    (until a call used the default). An open of a traced file that is not
    Python is seen (an audit hook). At exit what was seen is written to
    that folder as PGID.PID.reach (nothing else is written, and nothing
    printed)."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "<<REACH>>"),
                  encoding="utf-8") as f:
            out, *spec = f.read().splitlines()
    except (OSError, ValueError):
        return
    mon = getattr(sys, "monitoring", None)
    if mon is None or not spec:
        return
    try:
        mon.use_tool_id(<<TOOL>>, "byte-proof-reach")
    except ValueError:
        return
    files, data, watch, backs, nested, probes, defaults, rows_of = set(), set(), {}, {}, {}, {}, {}, {}
    for row in spec:
        f = row.split("\\t")
        if f[0] == "P":
            files.add(f[1])
        elif f[0] == "N":
            data.add(f[1])
        elif f[0] == "B":
            watch[(f[1], int(f[2]))] = frozenset(int(x) for x in f[3].split(","))
        elif f[0] == "J":
            k, loop = (f[1], int(f[2])), int(f[3])
            backs.setdefault(k, {}).update((int(x), loop) for x in f[4].split(","))
            nested[k + (loop,)] = tuple(int(x) for x in f[5].split(",") if x)
        elif f[0] == "R":
            k = (f[1], int(f[2]))
            probes.setdefault(k, {})[int(f[5])] = (int(f[3]), int(f[4]))
            rows_of.setdefault(k + (int(f[4]),), []).append(int(f[3]))
        elif f[0] == "D":
            defaults.setdefault((f[1], int(f[2])), {})[f[3]] = f[4]
    if defaults:
        import ast
        defaults = {k: {p: ast.literal_eval(v) for p, v in d.items()} for k, d in defaults.items()}
    pending = {k + need for k, p in probes.items() for need in p.values()}
    key, alive, state = {}, [], {}
    lines, entered, dests, looped, used, opened = set(), set(), {}, set(), set(), set()
    off = mon.DISABLE
    ev = mon.events
    shown = frozenset(os.path.basename(f) for f in data)

    def opens(event, args):
        if event == "open" and args and isinstance(args[0], (str, bytes)):
            path = os.fsdecode(args[0])
            if os.path.basename(path) in shown:
                try:
                    path = os.path.realpath(path)
                except (OSError, ValueError):
                    return
                if path in data:
                    opened.add(path)
    if data:
        sys.addaudithook(opens)

    def same(v, lit):
        if type(v) is not type(lit):
            return False
        if type(lit) is tuple:
            return len(v) == len(lit) and all(same(a, b) for a, b in zip(v, lit))
        return v == lit

    def needed(k, loop):
        return any(k + (r, inner) in pending for inner in (loop,) + nested.get(k + (loop,), ())
                   for r in rows_of.get(k + (inner,), ()))

    def held(k, at):
        """Whether another event is still needed at instruction `at`: a
        line event disabled there skips the instruction's own events that
        once (CPython 3.12)."""
        w = watch.get(k)
        if w and at in w and len(dests.get(k + (at,), ())) < 2:
            return True
        p = probes.get(k)
        if p and at in p and k + p[at] in pending:
            return True
        b = backs.get(k)
        return bool(b and at in b and needed(k, b[at]))

    def line(code, row):
        k = key.get(id(code))
        if k is None:
            return off
        lines.add(k + (row,))
        if k in watch or k in probes:
            return None if held(k, sys._getframe(1).f_lasti) else off
        return off

    def start(code, _offset):
        k = key.get(id(code))
        if k is None:
            return off
        entered.add(k)
        keep = False
        if k in probes:
            state.pop((id(sys._getframe(1)),) + k, None)
            keep = any(needed(k, loop) for loop in set(backs.get(k, {}).values()))
        d = defaults.get(k)
        if d:
            given = sys._getframe(1).f_locals
            for p in list(d):
                if p in given and same(given[p], d[p]):
                    used.add(k + (p,))
                    del d[p]
            keep = keep or bool(d)
        return None if keep else off

    def branch(code, at, dest):
        k = key.get(id(code))
        w = watch.get(k) if k is not None else None
        if not w or at not in w:
            return off
        seen = dests.setdefault(k + (at,), set())
        seen.add(dest)
        return off if len(seen) > 1 else None

    def jump(code, at, _dest):
        k = key.get(id(code))
        b = backs.get(k) if k is not None else None
        loop = b.get(at) if b else None
        if loop is None or not needed(k, loop):
            return off
        st = state.setdefault((id(sys._getframe(1)),) + k, {})
        s = st.setdefault(loop, [0, {}])
        s[0] += 1
        for inner in nested.get(k + (loop,), ()):
            st.pop(inner, None)
        return None

    def instruction(code, at):
        k = key.get(id(code))
        p = probes.get(k) if k is not None else None
        need = p.get(at) if p else None
        if need is None or k + need not in pending:
            return off
        row, loop = need
        s = state.setdefault((id(sys._getframe(1)),) + k, {}).setdefault(loop, [0, {}])
        last = s[1].get(row)
        if last is not None and last != s[0]:
            pending.discard(k + need)
            looped.add(k + need)
            return off
        s[1][row] = s[0]
        return None
    for event, callback in ((ev.LINE, line), (ev.PY_START, start), (ev.BRANCH, branch),
                            (ev.JUMP, jump), (ev.INSTRUCTION, instruction)):
        mon.register_callback(<<TOOL>>, event, callback)
    kind = type(_reach.__code__)

    def trace(code, path):
        order = []

        def walk(c):
            order.append(c)
            for x in c.co_consts:
                if isinstance(x, kind):
                    walk(x)
        walk(code)
        for idx, c in enumerate(order):
            k = (path, idx)
            key[id(c)] = k
            alive.append(c)
            events = ev.LINE | ev.PY_START
            if k in watch:
                events |= ev.BRANCH
            if k in probes:
                events |= ev.JUMP | ev.INSTRUCTION
            mon.set_local_events(<<TOOL>>, c, events)
    from importlib import _bootstrap_external
    loader = _bootstrap_external.SourceLoader
    get_code = loader.get_code

    def traced(self, fullname):
        code = get_code(self, fullname)
        try:
            path = os.path.realpath(self.get_filename(fullname))
        except (ImportError, OSError, TypeError, ValueError):
            return code
        if code is not None and path in files:
            trace(code, path)
        return code
    loader.get_code = traced

    def dump():
        if not (lines or entered or opened):
            return
        rows = ["L\\t%s\\t%d\\t%d" % k for k in lines]
        rows += ["E\\t%s\\t%d" % k for k in entered]
        rows += ["B\\t%s\\t%d\\t%d\\t%d" % (k + (d,)) for k, seen in dests.items() for d in seen]
        rows += ["R\\t%s\\t%d\\t%d\\t%d" % k for k in looped]
        rows += ["D\\t%s\\t%d\\t%s" % k for k in used]
        rows += ["O\\t%s" % f for f in opened]
        tmp = os.path.join(out, "%d.%d.tmp" % (os.getpgrp(), os.getpid()))
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write("\\n".join(rows) + "\\n")
            os.replace(tmp, tmp[:-4] + ".reach")
        except OSError:
            pass
    import atexit
    atexit.register(dump)


_reach()


def _load_next():
    import importlib.util
    mod = sys.modules.get("_taxjson_shimchain")
    if mod is None:
        spec = importlib.util.spec_from_file_location("_taxjson_shimchain", <<CHAIN>>)
        mod = sys.modules["_taxjson_shimchain"] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    mod.load_next(__file__)


_load_next()
'''


# ------------------------------------------------------------- isolation
def shim_text() -> str:
    return (_SHIM.replace("<<EVENTS>>", repr(tuple(sorted(NETWORK_EVENTS))))
            .replace("<<NOTE>>", NETWORK_NOTE).replace("<<CHAIN>>", repr(str(REPO / "tests" / "_shimchain.py")))
            .replace("<<REACH>>", REACH_FILE).replace("<<TOOL>>", str(REACH_TOOL)))


@functools.cache
def shim_dir() -> Path:
    """A folder of this process's own holding the network shim as
    sitecustomize.py, first on every child's PYTHONPATH (`child_env`);
    removed at exit."""
    import atexit
    folder = Path(tempfile.mkdtemp(prefix="byte-proof-shim-"))
    (folder / "sitecustomize.py").write_text(shim_text(), encoding="utf-8")
    atexit.register(shutil.rmtree, folder, True)
    return folder


_PROBE = ("import ctypes, os\nprint(os.readlink('/proc/self/ns/net'))\nprint(os.getuid())\n"
          "print(' '.join(sorted(l.split(':')[0].strip() for l in "
          "open('/proc/self/net/dev').read().splitlines()[2:])))\n"
          "s = ctypes.c_int()\nctypes.CDLL(None).prctl(2, ctypes.byref(s), 0, 0, 0)\nprint(s.value)\n")


@functools.cache
def isolation_problem() -> str:
    """"" when a child can run in a network namespace of its own (`unshare
    --user --map-current-user --net`: another namespace than this process's,
    the caller's user id, no interface but the loopback, which is down)
    with a parent-death signal (`setpriv --pdeathsig KILL`: SIGKILL kept
    through unshare); else why not."""
    if not (shutil.which("setpriv", path=PATH) and shutil.which("unshare", path=PATH)):
        return "no `setpriv` and `unshare` (util-linux) on PATH"
    try:
        own = os.readlink("/proc/self/ns/net")
        out, code = run_child(_wrap([sys.executable, "-I", "-S", "-c", _PROBE]), Path("/"),
                              {"PATH": PATH, "LANG": "C.UTF-8"}, 60)
    except (OSError, SystemExit) as e:
        return f"`{_WRAP_TEXT}` could not run: {e}"
    said = out["stdout"].decode("utf-8", "replace").split("\n")
    if code != 0 or len(said) < 4:
        return f"`{_WRAP_TEXT}` failed (exit {code}): {out['stderr'].decode('utf-8', 'replace').strip()[-300:]}"
    if said[0] == own or said[1] != str(os.getuid()) or said[2] != "lo" or said[3] != str(int(signal.SIGKILL)):
        return f"`{_WRAP_TEXT}` gave {said[:4]}, not a namespace of its own with SIGKILL at its parent's death"
    return ""


_WRAP_TEXT = f"setpriv {' '.join(PDEATH)} -- unshare {' '.join(NETNS)} --"


def _wrap(argv: list) -> list:
    return [shutil.which("setpriv", path=PATH), *PDEATH, "--", shutil.which("unshare", path=PATH), *NETNS,
            "--", *argv]


def isolated(argv: list) -> list:
    """`argv` in a network namespace of its own, killed if the thread that
    starts it dies; refused (SystemExit) when no namespace can be made: no
    child runs without one."""
    why = isolation_problem()
    if why:
        raise SystemExit(f"byte proof: refused: no child runs outside a network namespace, and "
                         f"none can be made here: {why}")
    return _wrap(argv)


def isolation() -> dict:
    """What proof.json records of the isolation (--compare refuses two that
    differ)."""
    return {"namespace": _WRAP_TEXT, "refused": sorted(NETWORK_EVENTS),
            "shim": hashlib.sha256(shim_text().replace(str(REPO), "<tree>").encode()).hexdigest()[:16]}


# ------------------------------------------------------------- the clock
def epoch(at: str) -> int:
    """`at` (ISO 8601 with its offset) as whole seconds since the epoch."""
    when = datetime.datetime.fromisoformat(at)
    if when.tzinfo is None:
        raise ValueError(f"{at}: an instant needs its offset from UTC (2026-11-18T21:30:00-05:00)")
    return int(when.timestamp())


def instant_label(at: str) -> str:
    return datetime.datetime.fromisoformat(at).strftime("%Y%m%dT%H%M")


def set_times(root: Path, at: str) -> None:
    """`root` and every file and folder below it dated FILE_AGE before `at`."""
    ns = (epoch(at) - FILE_AGE) * 1_000_000_000
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            os.utime(os.path.join(dirpath, name), ns=(ns, ns), follow_symlinks=False)
    os.utime(root, ns=(ns, ns), follow_symlinks=False)


_RATES: dict = {}


def make_home(home: Path, at: str) -> None:
    """A synthetic HOME (tests/_hermetic `home_env`'s folders): its rate
    cache's weekdays to a week past the instant's day, not today's."""
    day = datetime.datetime.fromisoformat(at).date()
    if day not in _RATES:
        _RATES[day] = json.dumps(_hermetic.rate_cache(today=day))
    for sub in (".cache", ".config", ".local/share"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    (home / ".currency_price_cache.json").write_text(_RATES[day], encoding="utf-8")


def child_env(at: str, home: Path, tmp: Path, width=None, extra=()) -> dict:
    """Every child's environment, made here and nothing inherited: this
    checkout's code under the network shim and the clock shim, the clock
    at `at` in the zone TZ, `home` (and its XDG folders) and `tmp`,
    offline, TAXJSON_WIDTH `width` (None: unset), and a pass's own
    variables (`extra`: NO_COLOR, COLUMNS)."""
    env = {
        "PATH": PATH, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TERM": "xterm-256color", "TZ": TZ,
        "HOME": str(home), "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_CONFIG_HOME": str(home / ".config"), "XDG_DATA_HOME": str(home / ".local" / "share"),
        "TMPDIR": str(tmp),
        "PYTHONPATH": os.pathsep.join((str(shim_dir()), str(_CLOCK), str(REPO / "src"))),
        "TAXJSON_OFFLINE": "1", "TAXJSON_TEST_CLOCK_AT": str(epoch(at)),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CEILING_DIRECTORIES": str(Path(tempfile.gettempdir()).resolve()),
    }
    for proxy in ("http_proxy", "https_proxy", "all_proxy"):
        env[proxy] = env[proxy.upper()] = _CLOSED_PORT
    env["no_proxy"] = env["NO_PROXY"] = ""
    if width is not None:
        env["TAXJSON_WIDTH"] = str(width)
    env.update(dict(extra))
    return env


def env_shape() -> dict:
    """child_env with its folders, its instant and its width named, as
    proof.json records it (--compare refuses two that differ); a pass's
    own variables are in PASSES."""
    home, tmp = Path("/HOME-OF-A-RUN"), Path("/TMPDIR-OF-A-RUN")
    env = child_env(INSTANTS[0], home, tmp, 0)
    env.update(TAXJSON_WIDTH="<width>", TAXJSON_TEST_CLOCK_AT="<instant>",
               GIT_CEILING_DIRECTORIES="<the temp folder>")
    labels = {str(home): "<home>", str(tmp): "<tmp>", str(REPO): "<tree>", str(shim_dir()): "<shim>"}
    for spelling in sorted(labels, key=len, reverse=True):
        env = {k: v.replace(spelling, labels[spelling]) for k, v in env.items()}
    return env


# ------------------------------------------------------------- commands
class Line(NamedTuple):
    """A command line: the folder it runs in (`.` the run's), its argv, the
    exit it states (None), its stdin (None), its edits ((op, path, text)),
    the commands run before it (argv strings), the edits made after those
    (`# edit:` notes written after a `# before:`)."""
    cwd: str
    argv: str
    expect: Optional[int] = None
    stdin: Optional[str] = None
    edits: tuple = ()
    before: tuple = ()
    after: tuple = ()


def book_kind(book: str) -> str:
    return _golden.meta(book).get("kind", "project")


def _edit_notes(edits) -> str:
    return "".join(f" # edit: {op} {path}" + (f" {text}" if text else "") for op, path, text in edits)


def command(cwd: str, argv: str, expect=None, stdin=None, edits=(), before=(), after=()) -> str:
    """A command as the proof names it: `ARGV`, or `FOLDER: ARGV` for one
    run in another folder of the project (from the run's), then its notes
    (`# exit N`, `# stdin: TEXT`, each `# edit: OP PATH [TEXT]`, each
    `# before: ARGV`, each edit made after them)."""
    argv = shlex.join(shlex.split(argv))
    out = argv if cwd in ("", ".") else f"{cwd}: {argv}"
    if expect is not None:
        out += f" # exit {expect}"
    if stdin is not None:
        out += f" # stdin: {stdin}"
    out += _edit_notes(edits)
    for argv in before:
        out += f" # before: {shlex.join(shlex.split(argv))}"
    return out + _edit_notes(after)


def parse_command(cmd: str) -> Line:
    """The Line of a command; a note that is none of the four is refused
    (ValueError). An `# edit:` after a `# before:` is made after it."""
    head, *notes = _NOTES.split(cmd)
    expect, stdin, edits, before, after = None, None, [], [], []
    for note in notes:
        m = _EXIT.fullmatch(note)
        if m and expect is None:
            expect = int(m.group(1))
            continue
        m = _STDIN.fullmatch(note)
        if m and stdin is None and m.group(1):
            stdin = m.group(1)
            continue
        m = _EDIT.fullmatch(note)
        if m and m.group(1) in EDITS and (m.group(1) == "append" or not m.group(3)):
            (after if before else edits).append((m.group(1), m.group(2), m.group(3) or ""))
            continue
        m = _BEFORE.fullmatch(note)
        if m and not after:
            before.append(m.group(1))
            continue
        raise ValueError(f"`# {note}`: not a note (`# exit N`, once; `# stdin: TEXT`, once; "
                         f"`# edit: {'|'.join(EDITS)} PATH`, `append PATH TEXT`; `# before: ARGV`, "
                         "never after an edit made after one)")
    first, _, rest = head.strip().partition(" ")
    if first.endswith(":") and rest.strip():
        return Line(first[:-1], rest.strip(), expect, stdin, tuple(edits), tuple(before), tuple(after))
    return Line(".", head.strip(), expect, stdin, tuple(edits), tuple(before), tuple(after))


def split_command(cmd: str) -> tuple:
    """(folder, argv) of a command."""
    return parse_command(cmd)[:2]


def stdin_bytes(text) -> bytes:
    """A line's stated stdin as typed: `\\n` a line break, `\\\\` a
    backslash, and a line break after the last answer."""
    if text is None:
        return b""
    return (re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), text) + "\n").encode()


def command_name(cmd: str) -> str:
    """The folder a command records under (tests/_golden.py `view_name`,
    then its stdin and its edits); past NAME_MAX characters, cut and the
    digest of the whole name added."""
    line = parse_command(cmd)
    name = _golden.view_name({"cwd": line.cwd, "argv": line.argv})
    if line.stdin is not None:
        name += "~stdin-" + line.stdin
    for op, path, text in line.edits:
        name += f"~{op}-{path}" + (f"-{text}" if text else "")
    for argv in line.before:
        name += f"~before-{argv}"
    for op, path, text in line.after:
        name += f"~then-{op}-{path}" + (f"-{text}" if text else "")
    name = re.sub(r"[^A-Za-z0-9_.~-]+", "_", name).strip("_")
    if len(name) > NAME_MAX:
        name = name[:NAME_MAX - 13] + "~" + hashlib.sha256(name.encode()).hexdigest()[:12]
    return name


def _view_command(view) -> str:
    cwd = view.get("cwd", ".") if isinstance(view, dict) else "."
    return command(cwd, shlex.join(_golden.view_argv(view)))


def book_commands(book: str) -> list:
    """The book's views, once each (a view in another folder as
    `FOLDER: ARGV`)."""
    out = []
    for v in _golden.meta(book).get("views", []):
        c = _view_command(v)
        if c not in out:
            out.append(c)
    return out


@functools.cache
def registry() -> dict:
    """{`tjs` command: {its option (the first long spelling): every
    spelling}} of the command registry (cli/parser `_build_parser`; help
    left out)."""
    from taxjson.cli.parser import _build_parser
    _p, sub = _build_parser()
    out = {}
    for name, sp in sorted(sub.choices.items()):
        opts = {}
        for a in sp._actions:
            if a.option_strings and not isinstance(a, argparse._HelpAction):
                key = next((s for s in a.option_strings if s.startswith("--")), a.option_strings[0])
                opts[key] = tuple(a.option_strings)
        out[name] = opts
    return out


def option_forms(book: str) -> list:
    """The option forms of a project book (GEN_*): each registry command's
    default form, `--details` and `--json` where it has them."""
    if book_kind(book) != "project":
        return []
    out = []
    for name, opts in registry().items():
        if name in DENIED or name in GEN_SKIP or book not in GEN_BOOKS.get(name, (book,)):
            continue
        args = GEN_ARGS.get(name, "")
        if not isinstance(args, str):
            args = args.get(book)
            if args is None:
                continue
        base = f"{name} {args}".strip()
        for form in [base] + [f"{base} {flag}" for flag in ("--details", "--json") if flag in opts]:
            if (name, form) not in GEN_NOT:
                out.append(command(".", form, 2 if book in GEN_EXITS.get(name, ()) else None))
    return out


def default_commands(book: str) -> list:
    """What --record runs without --argv: the book's views
    (book_commands), its EXTRA_COMMANDS and OPTION_LINES, then its option
    forms, once each (a form under a name already recorded left out)."""
    out = book_commands(book)
    names = {command_name(c) for c in out}
    extra = [command(*parse_command(line)) for b, line in EXTRA_COMMANDS + OPTION_LINES if b == book]
    for c in extra + option_forms(book):
        if command_name(c) not in names:
            out.append(c)
            names.add(command_name(c))
    return out


def first_instant_only(book: str) -> list:
    """The default commands recorded at the first instant only: the option
    forms and OPTION_LINES, those no view or EXTRA_COMMANDS line records.
    They reach the registry's options; the second instant's branches
    (January to April, the clock's date against a file's) are the dates',
    which the views, EXTRA_COMMANDS and every run reach at both."""
    hand = {command_name(c) for c in book_commands(book)}
    hand |= {command_name(line) for b, line in EXTRA_COMMANDS if b == book}
    return [c for c in default_commands(book) if command_name(c) not in hand]


def read_argv(path: Path, books: list) -> dict:
    """{book: [command]} of an --argv file's lines."""
    cmds = {b: [] for b in books}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        book, _, rest = line.partition(" ")
        try:
            parsed = parse_command(rest.strip())
        except ValueError as e:
            raise SystemExit(f"{path}:{n}: {e}") from None
        if not shlex.split(parsed.argv):
            raise SystemExit(f"{path}:{n}: no command after {book!r}")
        if book != "*" and book not in cmds:
            raise SystemExit(f"{path}:{n}: {book!r} is not a book recorded here: {', '.join(books)}")
        c = command(*parsed)
        for b in [x for x in books if book_kind(x) == "project"] if book == "*" else [book]:
            refuse({b: [c]}, f"{path}:{n}: ")
        if book != "*" and book_kind(book) != "project":
            raise SystemExit(f"{path}:{n}: {book} is an engine book: it has no project, "
                             "only its run is recorded")
        for b in [x for x in books if book_kind(x) == "project"] if book == "*" else [book]:
            if c not in cmds[b]:
                cmds[b].append(c)
    return cmds


def all_commands(books: list, argv_file=None) -> tuple:
    """({book: [command]}, {book: [command at the first instant only]}):
    each book's default commands, then an --argv file's lines (added,
    never in their stead; at every instant)."""
    out = {b: default_commands(b) for b in books}
    once = {b: first_instant_only(b) for b in books}
    if argv_file is not None:
        for b, cs in read_argv(argv_file, books).items():
            names = {command_name(c): c for c in out[b]}
            for c in cs:
                if names.get(command_name(c), c) != c:
                    raise SystemExit(f"{argv_file}: {b}: `{c}` records under the name of "
                                     f"`{names[command_name(c)]}`")
                if c not in out[b]:
                    out[b].append(c)
                    names[command_name(c)] = c
                once[b] = [x for x in once[b] if x != c]
    return out, {b: cs for b, cs in once.items() if cs}


def subcommand(argv: str) -> str:
    """The `tjs` command an argv runs (after -C/--dir DIR)."""
    words = shlex.split(argv)
    i = 0
    while i < len(words):
        w = words[i]
        if w in _GLOBAL_WITH_VALUE:
            i += 2
        elif w.startswith("-"):
            i += 1
        else:
            return w
    return ""


def colours(words: list) -> bool:
    """Whether the `nocolor` pass runs whatever the terminal showed: a
    command or tool of COLOURS."""
    return subcommand(shlex.join(words)) in COLOURS


# ------------------------------------------------------------- the guard
def _spellings(word: str) -> list:
    """Every spelling in `word` a command could take as a path: the word,
    after a leading `@`, each part between `=`, `,` and `:`, and each tail
    of a short option (its glued value: `-o/x`, `-C../..`)."""
    out = {word, word.lstrip("@"), *re.split(r"[=,:]", word)}
    if word.startswith("-") and not word.startswith("--"):
        out.update(word[i:] for i in range(2, len(word)))
    return sorted(p for p in out if p)


def _outside(part: str, base: str) -> str:
    """Why `part`, read from the folder `base` of the copy, names a path
    outside it, or "" (by its spelling)."""
    if part.startswith(("/", "~")) or "\0" in part:
        return f"`{part}`: a path outside the copy (absolute, or `~`)"
    norm = os.path.normpath(os.path.join("p", base, part))
    if norm != "p" and not norm.startswith("p" + os.sep):
        return f"`{part}`: a path above the copy"
    return ""


def _escapes(root: Path, base: str, part: str) -> str:
    """Why `part`, read from `root`/`base`, resolves outside `root` (through
    a link), or ""."""
    top = os.path.realpath(root)
    real = os.path.realpath(os.path.join(root, base, part))
    if real != top and not real.startswith(top + os.sep):
        return f"`{part}`: resolves outside the copy (a link out of it)"
    return ""


def refusal(cmd: str, run_dir: str = ".", root=None) -> str:
    """Why the proof will not run the command line `cmd` (a book's, its
    project run from `run_dir`), or "": a denied or unreviewed command or
    form, a stand-alone tool not in TOOLS, a glued `-C`, or an argument or
    an edit that names a path outside the copy (absolute, `~`, climbing out
    of it; with `root`, the copy or its source, through a link out of it)."""
    try:
        line = parse_command(cmd)
    except ValueError as e:
        return str(e)
    if line.before and any(op == "touch" for op, _p, _t in line.edits):
        return "a `touch` edit with `# before:`: the copy is dated again after the commands before it"
    for argv in line.before:
        why = refusal(argv if line.cwd in ("", ".") else f"{line.cwd}: {argv}", run_dir, root)
        if why:
            return f"`# before: {argv}`: {why}"
    words = shlex.split(line.argv)
    first = subcommand(line.argv)
    if first.startswith("taxjson-") or first in console_scripts():
        if first in TOOLS and words[0] == first:
            why = ""
        else:
            why = f"`{first}` is not a stand-alone tool the proof runs ({', '.join(sorted(TOOLS))})"
    elif first in DENIED:
        why = f"`{first}` is denied: {DENIED[first]}"
    elif first not in REVIEWED:
        why = f"`{first or line.argv}` is not a `tjs` command the proof has reviewed (REVIEWED)"
    else:
        why = next((f"`{first} {opt}` is denied: {reason}" for (sub, opt), reason in DENIED_FORMS.items()
                    if sub == first for w in words
                    if len(w.split("=")[0]) > 2 and opt.startswith(w.split("=")[0])), "")
    if not why:
        why = next((f"`{w}`: a glued -C (its folder is not checked)" for w in words
                    if w.startswith("-C") and len(w) > 2), "")
    if why:
        return why
    why = _outside(line.cwd, run_dir) if line.cwd not in ("", ".") else ""
    base = str(PurePosixPath(run_dir) / line.cwd)
    edits = line.edits + line.after
    paths = [(p, base) for w in words for p in _spellings(w)] + [(path, base) for _op, path, _t in edits]
    # An appended text's paths too (a setting naming a folder, a record a
    # path), read from the line's folder and from the edited file's.
    for _op, path, text in edits:
        for b in (base, os.path.normpath(os.path.join(base, os.path.dirname(path) or "."))):
            paths += [(p, b) for p in _text_paths(text)]
    for part, b in paths:
        why = why or _outside(part, b) or (_escapes(root, b, part) if root else "")
    return why


_TEXT_QUOTES = "\"'`[](){}<>;"


def _text_paths(text: str) -> list:
    """Every spelling in an appended TEXT a command could read as a path:
    each word's (`_spellings`), its quotes and brackets stripped."""
    out = []
    for word in text.split():
        for part in _spellings(word.strip(_TEXT_QUOTES)):
            part = part.strip(_TEXT_QUOTES)
            if part:
                out.append(part)
    return out


def refuse(commands: dict, where: str = "") -> None:
    """Exit 2, before anything runs, naming each line `refusal` refuses."""
    bad = []
    for book, cmds in commands.items():
        m = _golden.meta(book)
        root = _golden.GOLDEN / book / "project" if m.get("kind", "project") == "project" else None
        bad += [f"{where}{book}: {c}: {why}" for c in cmds
                for why in [refusal(c, m.get("run_dir", "."), root)] if why]
    if bad:
        print("byte proof: refused, nothing run:\n" + "\n".join(bad), file=sys.stderr)
        raise SystemExit(2)


def options_reached(commands: dict) -> set:
    """{(`tjs` command, its option)} the commands pass, each on a line
    that does not state a refusal (`# exit 2`: a refusal before any work
    shows nothing the option does; Fable's third review, TJ-4624)."""
    out = set()
    reg = registry()
    for cs in commands.values():
        for c in cs:
            line = parse_command(c)
            if line.expect == 2:
                continue
            argv = line.argv
            name = subcommand(argv)
            said = {w.split("=")[0] for w in shlex.split(argv) if w.startswith("-")}
            out |= {(name, key) for key, spellings in reg.get(name, {}).items() if said & set(spellings)}
    return out


def options_unreviewed(commands: dict) -> tuple:
    """(the registry's options neither passed by the commands nor in
    REVIEWED_OPTIONS, the REVIEWED_OPTIONS entries passed or no longer in
    the registry), each sorted."""
    reg, got = registry(), options_reached(commands)
    every = {(c, o) for c, opts in reg.items() for o in opts}
    neither = sorted(every - got - set(REVIEWED_OPTIONS))
    stale = sorted(k for k in REVIEWED_OPTIONS if k in got or k not in every)
    return neither, stale


def reach(commands: dict) -> tuple:
    """(the `tjs` commands reached, every `tjs` command, the stand-alone
    tools reached): each book's run and commands."""
    every = sorted(registry())
    firsts = {subcommand(split_command(c)[1]) for cs in commands.values() for c in cs}
    return sorted(({"run"} | firsts) & set(every)), every, sorted(firsts & set(console_scripts()))


# ------------------------------------------------------------- masking
def masked(data: bytes, labels: dict) -> bytes:
    """`data` with every `<` doubled, then each folder spelling replaced by
    its label (longest first: one spelling can end in another), and a build
    record's commit by `<commit:LENGTH>`; every other byte kept (an
    undecodable one too). One-to-one: a label is never text a command
    printed, two spellings never give one text."""
    text = data.decode("utf-8", "surrogateescape").replace("<", "<<")
    for spelling in sorted(labels, key=len, reverse=True):
        text = text.replace(spelling, labels[spelling])
    for pattern in _BUILD:
        text = pattern.sub(lambda m: f"{m.group(1)}<commit:{len(m.group(2))}>{m.group(3)}", text)
    return text.encode("utf-8", "surrogateescape")


@functools.cache
def code_fingerprint() -> str:
    """The package fingerprint every child's work/ records name: this
    checkout's src/taxjson (pipeline/freshness `_package_fingerprint`; its
    files do not change under a recording). Written to work/.deps/ records
    and work/.inputs_fingerprint.json (`code`), which are not recorded, and
    read by the refusal of books another taxjson built (cli/common), which
    no recording reaches: each checkout builds its own project."""
    import taxjson
    from taxjson.pipeline.freshness import _package_fingerprint
    if Path(taxjson.__file__).resolve().parent != (REPO / "src" / "taxjson").resolve():
        raise SystemExit(f"taxjson is imported from {Path(taxjson.__file__).parent}, not this "
                         f"checkout's {REPO / 'src' / 'taxjson'}")
    return _package_fingerprint()


def book_labels(run_top: Path, views_top: Path, pristine: Path) -> dict:
    """{spelling: label} of what a book's recording names beyond each
    pass's own folders: this checkout, its package fingerprint, the network
    shim's folder, the run's folders (its project, HOME and TMPDIR) and the
    views'."""
    out = {**folder_labels(REPO, "<tree>"), code_fingerprint(): "<code-fingerprint>",
           **folder_labels(shim_dir(), "<shim>"),
           **folder_labels(run_top, "<run-top>"), **folder_labels(pristine, "<pristine>"),
           **folder_labels(views_top, "<views-top>")}
    for sub, label in (("p", "<run>"), ("home", "<run-home>"), ("tmp", "<run-tmp>")):
        out.update(folder_labels(run_top / sub, label))
    for sub, label in (("views", "<views>"), ("views-home", "<views-home>"), ("views-tmp", "<views-tmp>")):
        out.update(folder_labels(views_top / sub, label))
    return out


def folder_labels(folder: Path, label: str) -> dict:
    """{spelling: label} for `folder` as given and as its real path."""
    spellings = {str(folder), os.path.realpath(folder)}
    if any("<" in s for s in spellings):
        raise SystemExit(f"{folder}: a folder the proof labels cannot hold `<`")
    return {s: label for s in spellings}


# ------------------------------------------------------------- running
@functools.cache
def console_scripts() -> dict:
    """{name: bin module} of this checkout's stand-alone tools (pyproject.toml's
    [project.scripts], `taxjson-NAME = "taxjson.bin._entry:MODULE"`): a
    command line may start with one (`taxjson-sum-gains work/margin_gains.json`),
    run as its console script runs it (`python -m taxjson.bin._entry MODULE`)."""
    import tomllib
    scripts = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    return {name: target.split(":", 1)[1] for name, target in scripts.items()
            if target.startswith("taxjson.bin._entry:")}


def _program(words: list) -> list:
    if words == [ENGINE_RUN]:
        return [sys.executable, "-P", "-c", _ENGINE]
    if words and words[0] in console_scripts():
        return _golden.python_module_argv("taxjson.bin._entry", [console_scripts()[words[0]], *words[1:]])
    return _golden.python_module_argv("taxjson.bin.taxjson_run", words)


def _kill(p) -> None:
    """SIGKILL to `p`'s process group (each child leads a session of its
    own: everything it started, unless that left the group)."""
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass


def _spawn(argv: list, cwd: Path, env: dict, **kw) -> subprocess.Popen:
    """`argv` started in a session and process group of its own."""
    return subprocess.Popen(argv, cwd=cwd, env=env, umask=0o077, start_new_session=True, **kw)


def _finish(p: subprocess.Popen, fds: dict, deadline: float, refused: str) -> tuple:
    """({kind: bytes}, exit) of `p`: each of `fds` ({fd: kind}) read to its
    end, and the leader's exit seen (a pidfd) before it is reaped. Its
    process group is killed as soon as the leader exits, so nothing it
    started outlives it (the leader's zombie still holds the group's id: it
    can be no other's), and on every way out: past `deadline` (then
    SystemExit(refused)), on an error. The leader is always reaped."""
    chunks = {kind: [] for kind in fds.values()}
    open_fds, exited = set(fds), False
    pidfd = os.pidfd_open(p.pid)
    try:
        while open_fds or not exited:
            left = deadline - time.monotonic()
            if left <= 0:
                raise SystemExit(refused)
            watch = sorted(open_fds if exited else open_fds | {pidfd})
            ready, _w, _x = select.select(watch, [], [], min(left, 1.0))
            for fd in ready:
                if fd == pidfd:
                    exited = True
                    _kill(p)                # what it started, holding a pipe or not
                    continue
                try:
                    data = os.read(fd, 65536)
                except OSError:             # EIO: every end of the terminal closed
                    data = b""
                if data:
                    chunks[fds[fd]].append(data)
                else:
                    open_fds.discard(fd)
    finally:
        os.close(pidfd)
        _kill(p)
        p.wait()
        _reach_settle(p.pid)
    return {kind: b"".join(parts) for kind, parts in chunks.items()}, p.returncode


def run_child(argv: list, cwd: Path, env: dict, timeout: int) -> tuple:
    """({"stdout", "stderr": bytes}, exit) of `argv`, stdin empty, as
    `invoke` runs a command (its group killed when it exits, refused past
    `timeout`)."""
    p = _spawn(argv, cwd, env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        return _finish(p, {p.stdout.fileno(): "stdout", p.stderr.fileno(): "stderr"},
                       time.monotonic() + timeout, f"{shlex.join(argv)}: still running after {timeout}s")
    finally:
        p.stdout.close()
        p.stderr.close()


class Terminal(NamedTuple):
    """The pty a command runs on: its size and what is typed at it."""
    rows: int = TTY_ROWS
    columns: int = TTY_COLUMNS
    stdin: bytes = b""


def invoke(words: list, cwd: Path, env: dict, how: str, term=None, timeout: int = TIMEOUT) -> tuple:
    """({file kind: bytes}, exit) of one command in a network namespace of
    its own (`isolated`): `how` "apart" (stdout and stderr on pipes apart:
    `stdout`, `stderr`), "merged" (stderr into stdout, as 2>&1: `merged`),
    "gone" (stdout a pipe whose reader closed before the command started,
    as `tjs sum | true`: `stderr` only), or on a terminal (`invoke_tty`)."""
    if how not in ("apart", "merged", "gone"):
        return invoke_tty(words, cwd, env, how, term or Terminal(), timeout)
    if how == "gone":
        reader, writer = os.pipe()
        os.close(reader)
        try:
            p = _spawn(isolated(_program(words)), cwd, env, stdin=subprocess.DEVNULL, stdout=writer,
                       stderr=subprocess.PIPE)
        finally:
            os.close(writer)
    else:
        p = _spawn(isolated(_program(words)), cwd, env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                   stderr=subprocess.STDOUT if how == "merged" else subprocess.PIPE)
    fds = {p.stdout.fileno(): "merged" if how == "merged" else "stdout"} if p.stdout is not None else {}
    if p.stderr is not None:
        fds[p.stderr.fileno()] = "stderr"
    try:
        return _finish(p, fds, time.monotonic() + timeout,
                       f"{shlex.join(words)}: still running after {timeout}s, piped ({how})")
    finally:
        for f in (p.stdout, p.stderr):
            if f is not None:
                f.close()


def invoke_tty(words: list, cwd: Path, env: dict, how: str = "tty", term=Terminal(),
               timeout: int = TIMEOUT) -> tuple:
    """({file kind: bytes}, exit) of one command in a network namespace of
    its own with stdin on a pty of `term`'s size (echo off; `term.stdin`
    typed, then QUEUED_EOFS end-of-files, as after Ctrl-D): `how` "tty"
    (stdout and stderr on it too), "outpiped" (stdout on a pipe, stderr on
    the terminal) or "errpiped" (stdout on the terminal, stderr on a pipe);
    what the terminal showed is `out`, a pipe's stream `stdout` or
    `stderr`. Refused (SystemExit) when it runs past `timeout` (`_finish`:
    its process group killed and the leader reaped, as on every way out)."""
    import fcntl
    import pty
    import struct
    import termios
    master, slave = pty.openpty()
    try:
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", term.rows, term.columns, 0, 0))
        attrs = termios.tcgetattr(slave)
        attrs[3] &= ~(termios.ECHO | termios.ECHONL)
        termios.tcsetattr(slave, termios.TCSANOW, attrs)
        os.write(master, term.stdin + attrs[6][termios.VEOF] * QUEUED_EOFS)
        p = _spawn(isolated(_program(words)), cwd, env, stdin=slave,
                   stdout=subprocess.PIPE if how == "outpiped" else slave,
                   stderr=subprocess.PIPE if how == "errpiped" else slave)
    except BaseException:
        os.close(master)
        raise
    finally:
        os.close(slave)
    kinds = {master: "out"}
    if p.stdout is not None:
        kinds[p.stdout.fileno()] = "stdout"
    if p.stderr is not None:
        kinds[p.stderr.fileno()] = "stderr"
    try:
        return _finish(p, kinds, time.monotonic() + timeout,
                       f"{shlex.join(words)}: still running after {timeout}s on the terminal "
                       f"({how}; a prompt that asks again after EOF?)")
    finally:
        os.close(master)
        for f in (p.stdout, p.stderr):
            if f is not None:
                f.close()


def snapshot(top: Path) -> dict:
    """{path below `top`: (kind, mode, content)}: each file (its bytes),
    folder and link (its target)."""
    out = {}
    top_s = str(top)
    cut = len(top_s) + 1
    for dirpath, dirnames, filenames in os.walk(top_s):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            st = os.lstat(p)
            rel = p[cut:]
            if stat.S_ISLNK(st.st_mode):
                out[rel] = ("link", 0, os.readlink(p).encode("utf-8", "surrogateescape"))
            elif stat.S_ISDIR(st.st_mode):
                out[rel] = ("dir", stat.S_IMODE(st.st_mode), b"")
            else:
                with open(p, "rb") as f:
                    out[rel] = ("file", stat.S_IMODE(st.st_mode), f.read())
    return out


def compared(rel: str) -> bool:
    """A file whose bytes are recorded: all but work/'s stage files (the
    golden books' `_compared` ledgers and reports, and every .diag, are)."""
    parts = rel.split("/")
    if "work" not in parts:
        return True
    return _golden._compared(rel) or (parts[-2] == "work" and parts[-1].endswith(".diag"))


def changes(before: dict, after: dict) -> tuple:
    """(lines, [(path, bytes)]): each entry added, removed, replaced by
    another kind, given another mode or changed; and the bytes of each file
    added or changed that is `compared`."""
    lines, files = [], []
    for rel in sorted(set(before) | set(after)):
        b, a = before.get(rel), after.get(rel)
        if b == a:
            continue
        if a is None:
            lines.append(f"removed {b[0]} {rel}")
            continue
        if b is None:
            lines.append(f"added {a[0]} {a[1]:04o} {rel}")
        elif b[0] != a[0]:
            lines.append(f"replaced {b[0]} by {a[0]} {a[1]:04o} {rel}")
        else:
            if b[1] != a[1]:
                lines.append(f"mode {b[1]:04o} to {a[1]:04o} {rel}")
            if b[2] != a[2]:
                lines.append(f"changed {a[0]} {rel}")
        if a[0] == "link":
            lines[-1] += f" -> {a[2].decode('utf-8', 'surrogateescape')}"
        elif a[0] == "file" and (b is None or b[2] != a[2] or b[0] != a[0]) and compared(rel):
            files.append((rel, a[2]))
    return lines, files


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def apply_edits(copy: Path, base: str, edits, at: str) -> None:
    """A line's edits (EDITS) to `copy`, each PATH read from its folder
    `base` and refused (SystemExit) outside the copy or through a link out
    of it: `touch` dates a file EDIT_AGE before `at`; `append` adds TEXT and
    a line break to a file (made when missing, with its folders), dated so;
    `remove` deletes a file or a link, its folder dated so."""
    ns = (epoch(at) - EDIT_AGE) * 1_000_000_000
    for op, rel, text in edits:
        path = copy / base / rel
        why = _outside(rel, base) or _escapes(copy, base, rel if op != "remove" else os.path.dirname(rel) or ".")
        if not why and (path.is_dir() and not path.is_symlink()):
            why = f"`{rel}`: a folder"
        if not why and op != "append" and not os.path.lexists(path):
            why = f"`{rel}`: no such file"
        if why:
            raise SystemExit(f"edit {op} {rel}: refused: {why}")
        if op == "remove":
            path.unlink()
            os.utime(path.parent, ns=(ns, ns))
            continue
        if op == "append":
            made, there = not path.exists(), path.parent
            while not there.exists():
                there = there.parent
            new = [d for d in path.parents if d not in there.parents and d != there]
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "ab") as f:
                f.write(text.encode("utf-8") + b"\n")
            for d in [*new, there] if made else []:
                os.utime(d, ns=(ns, ns))
        os.utime(path, ns=(ns, ns))


def _how(p: Pass) -> tuple:
    return ("apart", "merged") if p.streams == "piped" else (p.streams,)


def width_bound(seen: dict) -> bool:
    """Whether a command's bytes move with the width, by what its "every"
    passes printed (`seen`: {pass.kind: bytes}): its bytes at width 40 are
    not its bytes at width 0 (never wrapped), either stream, or the
    terminal did not show its bytes at width 0 (CR LF as a terminal ends a
    line). The width reaches a byte only through lib/out's `width` (its
    other readers ask `> 0`) and argparse's help (the terminal's): a
    command that prints the same at 0 and at 40, the narrowest width, and
    shows it on a terminal, prints it again at every width."""
    if "w0.stdout" not in seen or any(seen.get(f"w40.{k}") != seen.get(f"w0.{k}") for k in ("stdout", "stderr")):
        return True
    return seen.get("tty.out", b"").replace(b"\r\n", b"\n") != seen.get("w0.merged")


def runs_on(ps: Pass, seen: dict, words: list) -> bool:
    """Whether pass `ps` runs on a command (its `on`), by what the passes
    before it printed (`seen`: {pass.kind: bytes}): "stdout" when its
    width-0 stdout held a byte, "ascii" when any pass's bytes held one
    past ASCII, "width" when they move with the width (`width_bound`),
    "colour" for a command of COLOURS or one whose terminal output held an
    escape; "a|b" when either holds."""
    def holds(on: str) -> bool:
        if on == "every":
            return True
        if on == "stdout":
            return bool(seen.get("w0.stdout"))
        if on == "ascii":
            return any(max(data, default=0) > 0x7f for data in seen.values())
        if on == "width":
            return width_bound(seen)
        if on == "colour":
            return colours(words) or any(b"\x1b" in data for kind, data in seen.items() if kind.endswith(".out"))
        raise ValueError(f"{ps.name}: runs on {on!r}?")
    return any(holds(on) for on in ps.on.split("|"))


def checked(ps: Pass, line: Line) -> bool:
    """Whether the line's stated exit (or "not 2") is checked in pass
    `ps`: not where stdout's reader went away (`gone`), and for a line
    that states its stdin only where stdin is the terminal (on a pipe it
    is /dev/null: a command that needs a terminal refuses there)."""
    return ps.streams != "gone" and (line.stdin is None or bool(ps.columns))


def record_command(dest: Path, src: Path, run_dir: str, cmd: str, at: str, spellings: dict,
                   passes=PASSES, run=invoke) -> None:
    """One command in each pass, each on a fresh copy of `src` in a folder
    of its own (`p`, beside its HOME and TMPDIR, all dated FILE_AGE before
    `at`, then the line's edits made, the commands before it run, the
    copy dated again and the edits after them made), into dest/ (the
    module docstring's layout; `passes` the passes run, in order, each
    where `runs_on` says). Refused when it exits 2 and states no exit, or
    not as it states, in a pass where that is `checked`; before any pass,
    when `refusal` refuses it (run in `run_dir`, its folder included,
    against `src`). A line that states its stdin has it typed on the
    terminal; on a pipe its stdin is /dev/null. Only the recorded command's
    children count for the reach gate (`_reach_settle`), not those before
    it."""
    line = parse_command(cmd)
    if line.argv != ENGINE_RUN:
        why = refusal(command(".", *line[1:]), run_dir, src)
        if why:
            raise SystemExit(f"{cmd}: refused, nothing run: {why}")
    words = shlex.split(line.argv)
    typed = stdin_bytes(line.stdin)
    exits, seen, ran = [], {}, []
    for ps in passes:
        if not runs_on(ps, seen, words):
            continue
        ran.append(ps.name)
        meta = []
        for how in _how(ps):
            with tempfile.TemporaryDirectory(prefix="byte-proof-") as tmp:
                top = Path(tmp)
                copy, home, tmpdir = top / "p", top / "home", top / "tmp"
                shutil.copytree(src, copy, symlinks=True, copy_function=shutil.copy)
                make_home(home, at)
                tmpdir.mkdir()
                set_times(top, at)
                apply_edits(copy, run_dir, line.edits, at)
                for argv in line.before:
                    bouts, bcode = run(shlex.split(argv), copy / run_dir,
                                       child_env(at, home, tmpdir, 0), "apart", None)
                    if bcode != 0:
                        raise SystemExit(f"{cmd}: `# before: {argv}` exited {bcode}: " + masked(
                            bouts.get("stderr", b"")[-400:], {**folder_labels(top, "<top>")}).decode(
                                "utf-8", "replace"))
                if line.before:
                    set_times(top, at)
                apply_edits(copy, run_dir, line.after, at)
                labels = {**spellings, **folder_labels(top, "<top>"), **folder_labels(copy, "<project>"),
                          **folder_labels(home, "<home>"), **folder_labels(tmpdir, "<tmp>")}
                before = snapshot(top)
                term = Terminal(TTY_ROWS, ps.columns, typed) if ps.columns else None
                _REACH_LOCAL.keep = True
                try:
                    outs, code = run(words, copy / run_dir, child_env(at, home, tmpdir, ps.width, ps.env),
                                     how, term)
                finally:
                    _REACH_LOCAL.keep = False
                lines, files = changes(before, snapshot(top))
            if checked(ps, line):
                exits.append((f"{ps.name} {how}", code))
            for kind, data in sorted(outs.items()):
                seen[f"{ps.name}.{kind}"] = data = masked(data, labels)
                _write(dest / f"{ps.name}.{kind}", data)
            if how == "merged":
                meta.append(f"exit merged {code}")
                continue
            meta.append(f"exit {code}")
            meta += [masked(ln.encode("utf-8", "surrogateescape"), labels).decode("utf-8", "surrogateescape")
                     for ln in lines]
            for rel, data in files:
                _write(dest / f"{ps.name}.files" / rel, masked(data, labels))
        _write(dest / f"{ps.name}.meta", ("\n".join(meta) + "\n").encode("utf-8", "surrogateescape"))
    _write(dest / "passes", ("\n".join(ran) + "\n").encode())
    wrong = [f"{p} exited {c}" for p, c in exits if (c == 2 if line.expect is None else c != line.expect)]
    if wrong:
        why = ("exit 2 is a usage error: a line that means it says so (`ARGV # exit 2`)"
               if line.expect is None else f"the line states exit {line.expect}")
        raise SystemExit(f"{cmd}: {', '.join(wrong[:4])}{' ...' if len(wrong) > 4 else ''}: {why}")


def project(book: str, m: dict, folder: Path, at: str) -> tuple:
    """(pristine, root) in `folder`: the book's project as tests/golden
    holds it (made private, dated), and a copy of it as `tjs run
    --no-input` at width 0 and `at` left it, dated again; the run's exit
    status checked against the book's. An engine book: its book.json and
    inputs.json, and no run (None)."""
    pristine = folder / "pristine"
    if m.get("kind", "project") != "project":
        pristine.mkdir()
        for name in ("book.json", "inputs.json"):
            shutil.copy2(_golden.GOLDEN / book / name, pristine / name)
        _golden._make_private(pristine)
        set_times(pristine, at)
        return pristine, None
    shutil.copytree(_golden.GOLDEN / book / "project", pristine,
                    ignore=shutil.ignore_patterns(*_golden._GENERATED))
    _golden._make_private(pristine)
    set_times(pristine, at)
    root, home, tmpdir = folder / "p", folder / "home", folder / "tmp"
    shutil.copytree(pristine, root, symlinks=True)
    make_home(home, at)
    tmpdir.mkdir()
    outs, code = invoke(["run", "--no-input"], root / m.get("run_dir", "."),
                        child_env(at, home, tmpdir, 0), "apart")
    if code != m.get("exit", 0):
        raise SystemExit(f"{book}: tjs run exited {code}, expected {m.get('exit', 0)}:\n"
                         f"{outs['stdout'][-1500:].decode(errors='replace')}\n"
                         f"{outs['stderr'][-1500:].decode(errors='replace')}")
    set_times(root, at)
    return pristine, root


def view_states(views: list, root: Path, run_dir: str, wanted: list, folder: Path, at: str,
                run=invoke) -> dict:
    """{command: a copy of `root` in `folder`}: for one of the book's views,
    the project as the views before it left it; for another command in
    another folder, as every view left it. The views run in order on one
    copy (`folder`/views, with a HOME of its own), each at its own width,
    the files dated after each, as tests/_golden.py `_run_views` runs them
    (../2025 is made by `new-year 2025`, and its `run` comes before its
    `handoff`)."""
    if not wanted:
        return {}
    keys = [_view_command(v) for v in views]
    later = [c for c in wanted if c not in keys]
    stop = len(keys) if later else max(keys.index(c) for c in wanted)
    chain, home, tmpdir = folder / "views", folder / "views-home", folder / "views-tmp"
    shutil.copytree(root, chain, symlinks=True)
    make_home(home, at)
    tmpdir.mkdir()
    set_times(chain, at)
    states = {}
    for i, (view, key) in enumerate(zip(views, keys)):
        if key in wanted and key not in states:
            states[key] = folder / f"state{i}"
            shutil.copytree(chain, states[key], symlinks=True)
        if i >= stop:
            break
        cwd, argv = split_command(key)
        run(shlex.split(argv), chain / run_dir / cwd,
            child_env(at, home, tmpdir, _golden.view_width(view)), "apart")
        set_times(chain, at)
    if later:
        end = folder / "state-end"
        shutil.copytree(chain, end, symlinks=True)
        states.update({c: end for c in later})
    return states


# ------------------------------------------------------------- the tree
def _content_digest(root: Path, names) -> str:
    h = hashlib.sha256()
    for name in sorted(names):
        f = root / name
        if f.is_symlink():
            data = os.readlink(f).encode("utf-8", "surrogateescape")
        elif f.is_file():
            data = f.read_bytes()
        else:
            data = b"\0absent"
        h.update(name.encode("utf-8", "surrogateescape") + b"\0" + hashlib.sha256(data).digest())
    return h.hexdigest()[:16]


def _untracked(name: str) -> bool:
    return name in _NOT_TRACKED or name.endswith((".egg-info", ".pyc"))


def checkout(repo: Path = REPO, run=subprocess.run) -> str:
    """The checkout recorded, `COMMIT files DIGEST`: COMMIT `git rev-parse
    HEAD`, or `uncommitted` (a `git init` of an archive with nothing
    committed, where rev-parse fails and prints "HEAD"); DIGEST the bytes
    of each file git lists (`git ls-files`, a `git add -N` file too) as the
    checkout holds them. With no git (an archive as it came): `no git files
    DIGEST`, each file but the build and cache folders (_NOT_TRACKED). Two
    copies of one tree are one tree. Read only (no git write)."""
    def git(*args):
        try:
            r = run(["git", "-C", str(repo), *args], capture_output=True, check=False)
        except OSError:
            return 1, b""
        out = r.stdout or b""
        return r.returncode, out.encode() if isinstance(out, str) else out
    code, listed = git("ls-files", "-z")
    names = {n for n in listed.decode("utf-8", "surrogateescape").split("\0") if n} if code == 0 else set()
    if names and (repo / ".git").exists():
        code, head = git("rev-parse", "HEAD")
        head_s = head.decode(errors="replace").strip()
        if code != 0 or not head_s or head_s == "HEAD":
            head_s = "uncommitted"
        return f"{head_s} files {_content_digest(repo, names)}"
    walked = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if not _untracked(d)]
        walked += [(Path(dirpath) / f).relative_to(repo).as_posix() for f in filenames if not _untracked(f)]
    return f"no git files {_content_digest(repo, walked)}" if walked else ""


def tool_version() -> str:
    """The digest of TOOL_FILES as this checkout holds them."""
    h = hashlib.sha256()
    for rel in TOOL_FILES:
        h.update(rel.encode() + b"\0" + (REPO / rel).read_bytes() + b"\0")
    return h.hexdigest()[:16]


def python_version() -> str:
    return f"{sys.implementation.name} {platform.python_version()}"


_PACKAGES = ("import importlib.metadata as m, json\n"
             "print(json.dumps(sorted({f\"{d.metadata.get('Name') or '?'}=={d.version}\" "
             "for d in m.distributions()})))\n")


@functools.cache
def packages() -> dict:
    """The distributions a child sees (name==version, sorted) and their
    digest: run in a child, as the commands run (proof.json `packages`)."""
    with tempfile.TemporaryDirectory(prefix="byte-proof-pkgs-") as tmp:
        home = Path(tmp) / "home"
        home.mkdir()
        out, code = run_child(isolated([sys.executable, "-P", "-c", _PACKAGES]), Path(tmp),
                              child_env(INSTANTS[0], home, Path(tmp)), 120)
    if code != 0:
        raise SystemExit(f"byte proof: the children's packages could not be listed: "
                         f"{out['stderr'].decode(errors='replace')[-500:]}")
    names = json.loads(out["stdout"])
    return {"list": names, "digest": hashlib.sha256("\n".join(names).encode()).hexdigest()[:16]}


def _files(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p for p in sorted(root.rglob("*"))
            if p.is_file() and p.relative_to(root).as_posix() != PROOF}


def manifest(dest: Path) -> dict:
    """{path: [size, sha256]} of every file of a recording but proof.json."""
    return {n: [p.stat().st_size, hashlib.sha256(p.read_bytes()).hexdigest()] for n, p in _files(dest).items()}


def checkout_kind(tree: str) -> str:
    """"git" or "no git": what a command finds of the checkout it runs from
    (lib/channels `dev_checkout`: `help` lists the maintainer's commands in
    a clone, `parser-check` reads its fixtures); --compare refuses two
    recordings of other kinds."""
    return "no git" if (tree or "").startswith("no git") else "git"


def write_proof(dest: Path, commands: dict, passes=PASSES, tree: str = "", instants=INSTANTS,
                pkgs=None, once=None, reach=None, src=None) -> None:
    """DIR/proof.json: what --compare checks before the bytes (the instants,
    the zone, the file age, the passes, the environment, the isolation, the
    Python and its packages, the tool, the commands, the TMPDIR's length,
    every file's size and digest), what the reach gate reads (`src`: each
    file of this checkout's src/taxjson by its digest; `reach`: what ran in
    the files a `--reach-base` or `--reach-head` recording traced,
    `reach_record`, else None) and what it
    only shows (the checkout recorded, `tree`, and its package
    fingerprint; the day it was recorded)."""
    proof = {"format": FORMAT, "instants": list(instants), "tz": TZ, "file_age": FILE_AGE,
             "edit_age": EDIT_AGE, "passes": [list(p) for p in passes], "tty_rows": TTY_ROWS,
             "env": env_shape(), "isolation": isolation(), "python": python_version(),
             "packages": pkgs if pkgs is not None else packages(), "tool": tool_version(),
             "commands": commands, "once": once or {}, "tree": tree or "unknown",
             "checkout_kind": checkout_kind(tree), "tmpdir": tmpdir_shape(),
             "src": src if src is not None else src_digests(src_files(REPO)), "reach": reach,
             "recorded": datetime.date.today().isoformat(), "code_fingerprint": code_fingerprint(),
             "manifest": manifest(dest)}
    (dest / PROOF).write_text(json.dumps(proof, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def record(dest: Path, books: list, commands: dict, jobs: int = 1, passes=PASSES,
           instants=INSTANTS, once=None, reach_base=None, reach_head=None) -> int:
    """Each book run at each instant, then the run and its commands
    recorded (`once`: {book: [command]}, those of them at the first
    instant only); how many commands were recorded (the runs not counted).
    With `reach_base` (this checkout is HEAD; BASE a tree or a commit,
    `src_files`) or `reach_head` (this checkout is BASE), the files of
    src/taxjson that differ from the other tree are traced in every
    recorded child, and what the diff changes on this side watched
    (`reach_spec`, `reach_begin`), for the reach gate. A line `refusal`
    refuses stops it first (exit 2), DEST untouched, and so does a
    machine where no network namespace can be made."""
    if reach_base is not None and reach_head is not None:
        raise SystemExit("record with --reach-base (this is HEAD) or --reach-head (this is BASE), not both")
    refuse({b: commands.get(b, []) for b in books})
    why = isolation_problem()
    if why:
        raise SystemExit(f"byte proof: refused, nothing recorded: no network namespace can be "
                         f"made here, and no child runs without one: {why}")
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"{dest}: not empty; a recording goes into a folder of its own")
    for at in instants:
        if epoch(at) > time.time():
            raise SystemExit(f"{at}: the instant must be past, so each file a command writes is "
                             "newer than the files dated before it")
    this = src_files(REPO)
    side = "HEAD" if reach_base is not None else "BASE" if reach_head is not None else None
    other = src_files(reach_base if side == "HEAD" else reach_head) if side else None
    dest.mkdir(parents=True, exist_ok=True)
    tree, pkgs = checkout(), packages()
    if side:
        reach_begin(dest / ".reach", reach_spec(this, other, REPO, side))
    try:
        n = _record_books(dest, books, commands, jobs, passes, instants, once)
    finally:
        ran = reach_end() if side else None
    reach = reach_record(this, other, REPO, side, ran) if side else None
    write_proof(dest, commands, passes, tree, instants, pkgs, once, reach, src_digests(this))
    return n


def _record_books(dest: Path, books: list, commands: dict, jobs: int, passes, instants, once) -> int:
    n = 0
    for book in books:
        m = _golden.meta(book)
        engine = m.get("kind", "project") != "project"
        run_dir = m.get("run_dir", ".")
        cmds = commands.get(book, [])
        if engine and cmds:
            raise SystemExit(f"{book} is an engine book: it has no project, only its run is recorded")
        names = [command_name(c) for c in cmds]
        if len(set(names)) != len(names) or RUN in names:
            raise SystemExit(f"{book}: two commands record as one name, or as {RUN}: {names}")
        for at in instants:
            with tempfile.TemporaryDirectory(prefix="byte-proof-run-") as tmp, \
                    tempfile.TemporaryDirectory(prefix="byte-proof-views-") as vtmp:
                pristine, root = project(book, m, Path(tmp), at)
                spellings = book_labels(Path(tmp), Path(vtmp), pristine)
                if engine:
                    items = [(RUN, pristine, ".", f"{ENGINE_RUN} # exit 0")]
                else:
                    states = view_states(m.get("views", []), root, run_dir,
                                         [c for c in cmds if split_command(c)[0] != "."], Path(vtmp), at)
                    items = [(RUN, pristine, run_dir, f"run --no-input # exit {m.get('exit', 0)}")]
                    for c, name in zip(cmds, names):
                        if at != instants[0] and c in (once or {}).get(book, ()):
                            continue
                        cwd = split_command(c)[0]
                        items.append((name, states[c] if cwd != "." else root,
                                      str(PurePosixPath(run_dir) / cwd), c))

                def one(item, book=book, at=at, spellings=spellings):
                    name, src, rd, c = item
                    try:
                        record_command(dest / book / name / instant_label(at), src, rd, c, at,
                                       spellings, passes)
                    except SystemExit as e:
                        raise SystemExit(f"{book}, {instant_label(at)}: {e}") from None
                # The longest first (the run, and each line that runs it), so
                # the pool's last item is a short one.
                items.sort(key=lambda it: it[0] != RUN and subcommand(split_command(it[3])[1]) != "run")
                pool = ThreadPoolExecutor(max_workers=max(1, jobs))
                try:
                    list(pool.map(one, items))
                finally:                    # a refusal or Ctrl-C: nothing queued starts
                    pool.shutdown(wait=True, cancel_futures=True)
        n += len(names)
    return n


# ------------------------------------------------------------- the reach gate
def src_files(tree) -> dict:
    """{path below src/taxjson: bytes} of `tree`: a folder holding
    src/taxjson (a clone or an archive; the build and cache files left
    out, _NOT_TRACKED), or a commit of this checkout's git (`git
    archive`)."""
    folder = Path(tree)
    if folder.is_dir():
        top = folder / SRC
        if not top.is_dir():
            raise SystemExit(f"{tree}: no {SRC} in it")
        out = {}
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = sorted(d for d in dirnames if not _untracked(d))
            for name in filenames:
                p = Path(dirpath) / name
                if not _untracked(name) and p.is_file() and not p.is_symlink():
                    out[p.relative_to(top).as_posix()] = p.read_bytes()
        return out
    r = subprocess.run(["git", "-C", str(REPO), "archive", "--format=tar", f"{tree}^{{commit}}", SRC],
                       capture_output=True, check=False)
    if r.returncode != 0:
        raise SystemExit(f"{tree}: neither a folder nor a commit of {REPO}: "
                         f"{r.stderr.decode(errors='replace').strip()[:200]}")
    out = {}
    with tarfile.open(fileobj=io.BytesIO(r.stdout)) as t:
        for m in t.getmembers():
            name = m.name[len(SRC) + 1:]
            if m.isfile() and m.name.startswith(SRC + "/") and not any(_untracked(x) for x in name.split("/")):
                out[name] = t.extractfile(m).read()
    return out


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def src_digests(files: dict) -> dict:
    """{path: digest} of src_files: proof.json `src`, what --compare checks
    the trees it is given against."""
    return {rel: _digest(data) for rel, data in sorted(files.items())}


def _tree_file(tree, rel: str) -> Optional[bytes]:
    """The bytes of `rel` (from the checkout's top) in `tree` (as
    `src_files` reads it), or None."""
    folder = Path(tree)
    if folder.is_dir():
        p = folder / rel
        return p.read_bytes() if p.is_file() else None
    r = subprocess.run(["git", "-C", str(REPO), "show", f"{tree}:{rel}"], capture_output=True, check=False)
    return r.stdout if r.returncode == 0 else None


# What the children of a recording traced for the reach gate wrote
# (`reach_begin`, `_reach_settle`): by kind (`_RAN`), each a set of rows.
_REACH_LOCK = threading.Lock()
_RAN = ("lines", "entered", "branches", "loops", "defaults", "opened")
_REACH: dict = {"out": None, **{k: set() for k in _RAN}}
_REACH_LOCAL = threading.local()
# A trace's rows (the shim's `dump`): kind, how many fields, which are numbers.
_ROWS = MappingProxyType({"L": ("lines", 4, (2, 3)), "E": ("entered", 3, (2,)), "B": ("branches", 5, (2, 3, 4)),
                          "R": ("loops", 5, (2, 3, 4)), "D": ("defaults", 4, (2,)), "O": ("opened", 2, ())})
SIDES = ("BASE", "HEAD")


def reach_begin(out: Path, spec) -> None:
    """From now on every child traces what `spec` names (`reach_spec`'s
    rows, through shim_dir()/REACH_FILE: the network shim's `_reach`) and
    writes what ran to `out` at its exit."""
    out.mkdir(parents=True, exist_ok=True)
    with _REACH_LOCK:
        _REACH.update(out=out, **{k: set() for k in _RAN})
    (shim_dir() / REACH_FILE).write_text("\n".join([str(out), *spec]) + "\n", encoding="utf-8")


def reach_end() -> dict:
    """What the recorded children ran since reach_begin ({kind: {row}},
    `_RAN`, each row a tuple whose first field is a file's real path); the
    trace stopped, its folder removed."""
    (shim_dir() / REACH_FILE).unlink(missing_ok=True)
    with _REACH_LOCK:
        out, ran = _REACH["out"], {k: _REACH[k] for k in _RAN}
        _REACH.update(out=None, **{k: set() for k in _RAN})
    if out is not None:
        shutil.rmtree(out, ignore_errors=True)
    return ran


def _reach_settle(pgid: int) -> None:
    """After a child's group is killed and its leader reaped: what its
    processes wrote (`PGID.*.reach`) kept when it was a recorded command
    (`record_command`: `_REACH_LOCAL.keep`), dropped otherwise (a run that
    builds a copy, a view, a `# before:`, a probe)."""
    out = _REACH["out"]
    if out is None:
        return
    keep = getattr(_REACH_LOCAL, "keep", False)
    got = {k: set() for k in _RAN}
    for p in out.glob(f"{pgid}.*"):
        if keep and p.suffix == ".reach":
            try:
                rows = p.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                rows = []
            for row in rows:
                f = row.split("\t")
                kind = _ROWS.get(f[0])
                if kind and len(f) == kind[1] and all(f[i].isdigit() for i in kind[2]):
                    got[kind[0]].add(tuple(int(x) if i in kind[2] else x for i, x in enumerate(f) if i))
        p.unlink(missing_ok=True)
    if any(got.values()):
        with _REACH_LOCK:
            for k, rows in got.items():
                _REACH[k] |= rows


def _tree_digest(files: dict) -> str:
    return _digest(json.dumps(src_digests(files), sort_keys=True).encode())


def reach_record(this: dict, other: dict, root: Path, side: str, ran: dict) -> dict:
    """proof.json `reach` of a recording of `this` (src_files of `root`)
    traced against `other` as the comparison's `side` (`reach_spec`):
    the other tree's digest, the Python (a trace names bytecode offsets),
    the files traced (each that differs from `other`, by its digest) and
    what ran in them (`_RAN`, by path below src/taxjson: [code, row] a
    line, code an entered code object (its place in its module's code
    tree, `code_tree`), [code, offset, destination] a branch taken,
    [code, row, loop] a line run in two iterations of one entry of its
    loop, [code, parameter] a default a call used, and the files not
    Python a command opened)."""
    top = os.path.realpath(root / SRC)
    traced = traced_files(other, this)

    def rel(path):
        return path[len(top) + 1:] if path.startswith(top + os.sep) else None
    out = {"side": side, "other": _tree_digest(other), "python": platform.python_version(),
           "traced": {r: _digest(this[r]) for r in traced}}
    for kind in _RAN[:-1]:
        per = {}
        for row in ran.get(kind, ()):
            r = rel(row[0])
            if r in out["traced"] and r.endswith(".py"):
                per.setdefault(r, []).append(row[1] if kind == "entered" else list(row[1:]))
        out[kind] = {r: sorted(v) for r, v in sorted(per.items())}
    out["opened"] = sorted({rel(row[0]) for row in ran.get("opened", ())} & set(traced))
    return out


def traced_files(other: dict, this: dict) -> list:
    """The files of src/taxjson a recording of `this` traced against
    `other` traces: each of `this` that `other` lacks or holds other bytes
    of (a .py file by its code, another by its opens)."""
    return sorted(r for r, data in this.items() if other.get(r) != data)


def _decoded(data) -> str:
    """Python source bytes as text, by its own coding cookie (utf-8 by
    default; an undecodable byte kept as a surrogate)."""
    if data is None:
        return ""
    try:
        encoding = tokenize.detect_encoding(io.BytesIO(data).readline)[0]
        return data.decode(encoding)
    except (SyntaxError, LookupError, UnicodeDecodeError):
        return data.decode("utf-8", "surrogateescape")


def _source_lines(text: str) -> list:
    """`text`'s lines as tokenize and ast number them (row n at n - 1)."""
    return [ln.rstrip("\r\n") for ln in io.StringIO(text).readlines()]


def _normalized(text: str) -> list:
    """Each line of Python source as it is code: its comment cut and its
    end blank-stripped (a line holding only a comment or blanks is ""),
    but a line any part of a string spanning lines is on kept whole and
    never "" (a blank line of a help text is code; so are the blanks
    ending its first line)."""
    lines = _source_lines(text)
    out = [ln.rstrip() for ln in lines]
    inside = set()
    try:
        for t in tokenize.generate_tokens(io.StringIO(text).readline):
            if t.type == tokenize.COMMENT:
                r, c = t.start
                out[r - 1] = lines[r - 1][:c].rstrip()
            elif t.end[0] > t.start[0] and t.type not in (tokenize.NEWLINE, tokenize.NL):
                inside.update(range(t.start[0], t.end[0] + 1))
    except (tokenize.TokenError, SyntaxError):
        return out
    for r in inside:
        if r - 1 < len(lines):
            out[r - 1] = "\0" + lines[r - 1]
    return out


class Diff(NamedTuple):
    """What the diff from BASE to HEAD changes in a file (`diff_rows`):
    `head` HEAD's rows it adds or changes, `base` BASE's rows it deletes or
    replaces (in Python their code: a comment or a blank line aside);
    `gaps` (the HEAD row before a deletion, the first line deleted); and
    `head_raw`, `base_raw` the rows whose bytes changed but not their code
    (a comment, a blank line, a blank at a line's end, a line ending)."""
    head: list
    base: list
    gaps: list
    head_raw: list
    base_raw: list


def diff_rows(base: str, head: str, python: bool = True) -> Diff:
    """The Diff of `base` and `head` (decoded, `_decoded`: a coding cookie
    that changes how a line reads changes the line)."""
    a = _normalized(base) if python else _source_lines(base)
    b = _normalized(head) if python else _source_lines(head)
    raw = _source_lines(base)
    rows, old_rows, gaps = [], [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        new = [j + 1 for j in range(j1, j2) if b[j] or not python]
        old = [i + 1 for i in range(i1, i2) if a[i] or not python]
        rows += new
        old_rows += old
        if not new and old:
            gaps.append((j1, raw[old[0] - 1] if old[0] <= len(raw) else ""))
    ra, rb = io.StringIO(base).readlines(), io.StringIO(head).readlines()
    head_raw, base_raw = set(), set()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ra, rb, autojunk=False).get_opcodes():
        if tag != "equal":
            head_raw.update(range(j1 + 1, j2 + 1))
            base_raw.update(range(i1 + 1, i2 + 1))
    return Diff(rows, old_rows, gaps, sorted(head_raw - set(rows)), sorted(base_raw - set(old_rows)))


class Need(NamedTuple):
    """What a changed line of src/taxjson needs to have run, and how it is
    named when it did not: `order` how specific (0 a changed line, 1 a
    deletion, 2 a function whose body changed, 3 a module); `path` below
    src/taxjson; `row` and `text` the line named; `kind` and `keys` what
    must have been seen in the `side`'s recording (`satisfied`): "line"
    or "rows" (one of `keys`, (code, row), ran: code a code object's
    place in its module's code tree, `code_tree`), "entered" (the code
    object in `keys` started), "module" (any line of it ran), "branch"
    (`keys`' branch instructions, (code, offset, next offset, the outcome
    falling through means), went both ways), "loop" ((code, row, loop):
    the line ran in two iterations of one entry of the loop), "default"
    ((code, parameter): a call used the default), "text" (each fragment of
    `keys` shown in the recording's output), "opened" (a file not Python
    opened) or "never" (nothing a recording shows: a statement moved
    into or out of a try, a finally or a with; a default that is not a
    literal); `why` what is said of it; `side` "HEAD", or "BASE" for a
    line the diff deletes or replaces."""
    order: int
    path: str
    row: int
    text: str
    kind: str
    keys: frozenset = frozenset()
    why: str = ""
    side: str = "HEAD"


_SIMPLE = (ast.Expr, ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Return, ast.Raise, ast.Assert,
           ast.Delete, ast.Pass, ast.Break, ast.Continue, ast.Import, ast.ImportFrom, ast.Global,
           ast.Nonlocal, ast.TypeAlias)
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef, ast.GeneratorExp)
_COMPS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_CONDITIONALS = (ast.IfExp, ast.BoolOp, ast.Lambda) + _COMPS
# A branch instruction, and the outcome its falling through means: for
# FOR_ITER an item (its jump: the iterator exhausted); for the None tests,
# the value is None.
_BRANCH_OPS = MappingProxyType({"POP_JUMP_IF_FALSE": True, "POP_JUMP_IF_TRUE": False, "POP_JUMP_IF_NONE": False,
                                "POP_JUMP_IF_NOT_NONE": True, "FOR_ITER": True})


class _Inst(NamedTuple):
    idx: int        # its code object's place in the module's code tree
    offset: int
    opname: str
    argval: object
    pos: tuple      # (line, column, end line, end column)
    nxt: int        # the next instruction's offset: where falling through goes


class _Loop(NamedTuple):
    idx: int        # the code object it is in
    ident: int      # its first offset (FOR_ITER's, or a while's first)
    backs: tuple    # the offsets of its back-edges
    body: tuple     # the AST nodes run once per iteration
    node: object


class _Source(NamedTuple):
    rows: frozenset     # the lines an instruction is on (`_instructions`)
    stmts: tuple        # (first line (decorators too), last line, column, depth, node)
    lines: tuple
    tree: object
    at: dict            # {row: {code: [_Inst by offset]}}
    owner: dict         # {id(scope node): code}
    loops: tuple
    groups: dict        # {(code, position): (_Inst, ...)}: the branches the gate counts
    parents: dict       # {id(node): its parent}


def code_tree(code) -> list:
    """`code` and the code objects in its constants, depth first, in
    order: a code object's place in this list is how a trace names it
    (the network shim's `_reach` walks it the same way)."""
    out = []

    def walk(c):
        out.append(c)
        for k in c.co_consts:
            if isinstance(k, CodeType):
                walk(k)
    walk(code)
    return out


def _span(node) -> tuple:
    return (node.lineno, node.col_offset, node.end_lineno, node.end_col_offset)


def _inside(pos: tuple, node) -> bool:
    """Whether an instruction's position lies within `node`'s source."""
    line, col, end, end_col = pos
    if line is None or end is None:
        return False
    if col is None or end_col is None:
        return node.lineno <= line and end <= node.end_lineno
    return (node.lineno, node.col_offset) <= (line, col) and (end, end_col) <= (node.end_lineno, node.end_col_offset)


def _instructions(codes: list) -> tuple:
    """([_Inst], {row: {code: [_Inst]}}) of the code objects: each
    instruction that can start a line event (a code object's prologue up to
    its first RESUME, every RESUME and an instruction with no line aside)."""
    insts, at = [], {}
    for idx, c in enumerate(codes):
        ins = list(dis.get_instructions(c))
        first = next((n for n, x in enumerate(ins) if x.opname == "RESUME"), -1)
        for n, x in enumerate(ins):
            p = x.positions
            if n <= first or x.opname == "RESUME" or not p or not p.lineno:
                continue
            i = _Inst(idx, x.offset, x.opname, x.argval, (p.lineno, p.col_offset, p.end_lineno, p.end_col_offset),
                      ins[n + 1].offset if n + 1 < len(ins) else -1)
            insts.append(i)
            at.setdefault(p.lineno, {}).setdefault(idx, []).append(i)
    return insts, at


def _owners(tree, codes: list, at: dict) -> dict:
    """{id(scope node): code}: each function, lambda, class and generator
    expression's code object (by its name and first line; two on a line
    told apart by where their instructions are)."""
    names = {ast.Lambda: "<lambda>", ast.GeneratorExp: "<genexpr>"}
    by = {}
    for idx, c in enumerate(codes):
        by.setdefault((c.co_name, c.co_firstlineno), []).append(idx)
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, _SCOPES):
            continue
        first = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", ())])
        found = by.get((names.get(type(node), getattr(node, "name", "")), first), [])
        if len(found) > 1:
            found = [idx for idx in found
                     if any(_inside(i.pos, node) for r in range(node.lineno, node.end_lineno + 1)
                            for i in at.get(r, {}).get(idx, ()))] or found[:1]
        if found:
            out[id(node)] = found[0]
    return out


def _scope_code(node, parents: dict, owner: dict) -> int:
    """The code object a node's own instructions are in."""
    cur = parents.get(id(node))
    while cur is not None and not isinstance(cur, _SCOPES):
        cur = parents.get(id(cur))
    return 0 if cur is None else owner.get(id(cur), -1)


def _for_iter_at_iterable() -> bool:
    """Whether this interpreter puts a loop's FOR_ITER at its iterable's
    source (CPython 3.13, and 3.12 after 3.12.3) rather than the whole
    `for` statement's or comprehension's (3.12.3). The recordings and the
    analysis run on one interpreter, so this one's answer is the one
    `_loops` needs (TJ-4871)."""
    code = compile("for r in v:\n    pass\n", "<probe>", "exec", dont_inherit=True)
    head, = [x for x in dis.get_instructions(code) if x.opname == "FOR_ITER"]
    return (head.positions.lineno, head.positions.col_offset) == (1, 9)


_FOR_ITER_AT_ITERABLE = _for_iter_at_iterable()


def _loop_heads(node, insts: list) -> list:
    """[(FOR_ITER, innermost)] of a for loop or comprehension: each of
    its `for`s' FOR_ITER (one per generator), sorted, the innermost
    flagged, found at the iterable's span or the whole node's, as this
    interpreter places it (`_FOR_ITER_AT_ITERABLE`)."""
    def at(span):
        return sorted((i for i in insts if i.opname == "FOR_ITER" and i.pos == span),
                      key=lambda i: (i.idx, i.offset))
    if not _FOR_ITER_AT_ITERABLE:
        heads = at(_span(node))
        return [(h, n == len(heads) - 1) for n, h in enumerate(heads)]
    iters = [node.iter] if isinstance(node, ast.For) else [g.iter for g in node.generators]
    return [(h, n == len(iters) - 1) for n, it in enumerate(iters) for h in at(_span(it))]


def _loops(tree, insts: list, parents: dict, owner: dict, codes: list) -> tuple:
    """Each loop of the source with a back-edge: a for loop or a
    comprehension by its FOR_ITER (a comprehension of several `for`s, each;
    its body the innermost's; `_loop_heads`), a while loop by its first
    instruction and its body's (where its back-edges go). A back-edge goes
    to the first EXTENDED_ARG before its target when the target has any (a
    loop longer than one byte's jump: CPython 3.12 reports that offset as
    the jump's destination, and the FOR_ITER's own as the branch's). The
    back-edges are read from the code objects whole (`codes`): one an
    exception handler ends with has no line of its own."""
    out = []
    every = [(idx, x) for idx, c in enumerate(codes) for x in dis.get_instructions(c)]
    extended = {(idx, x.offset) for idx, x in every if x.opname == "EXTENDED_ARG"}
    jumps = [(idx, x.offset, x.argval) for idx, x in every if x.opname == "JUMP_BACKWARD"]

    def start(idx, offset):
        while (idx, offset - 2) in extended:
            offset -= 2
        return offset
    for node in ast.walk(tree):
        if isinstance(node, (ast.For,) + _COMPS):
            if isinstance(node, ast.For):
                body = tuple(node.body)
            else:
                gens = node.generators
                body = tuple(([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt])
                             + [x for g in gens for x in g.ifs] + [x for g in gens[1:] for x in (g.iter, g.target)])
            for h, innermost in _loop_heads(node, insts):
                to = start(h.idx, h.offset)
                backs = tuple(at for idx, at, dest in jumps if idx == h.idx and dest == to)
                if backs:
                    out.append(_Loop(h.idx, h.offset, backs, body if innermost else (), node))
        elif isinstance(node, ast.While) and node.body:
            idx = _scope_code(node, parents, owner)
            mine = [i for i in insts if i.idx == idx]
            firsts = [min((i.offset for i in mine if _inside(i.pos, part)), default=None)
                      for part in (node.test, node.body[0])]
            targets = {f for f in firsts if f is not None}
            backs = tuple(at for i, at, dest in jumps if i == idx and dest in targets)
            if backs:
                out.append(_Loop(idx, min(targets), backs, tuple(node.body), node))
    return tuple(out)


def _branch_groups(tree, insts: list) -> dict:
    """{(code, position, n): (_Inst, ...)}: the branch instructions of an
    if, elif, while, for (its exhaustion), conditional expression, and/or,
    comprehension, match case and except clause, grouped by the source
    they test (a while's test is compiled twice: one group, n 0); an
    and/or whose value is used puts each operand's jump at its own span,
    so those are a group each (n the jump's offset); an assert's and a
    with's exit test aside (neither goes the other way but on an
    error)."""
    asserts = [n for n in ast.walk(tree) if isinstance(n, ast.Assert)]
    withs = {_span(n) for n in ast.walk(tree) if isinstance(n, (ast.With, ast.AsyncWith))}
    bools = {_span(n) for n in ast.walk(tree) if isinstance(n, ast.BoolOp)}
    out = {}
    for i in insts:
        if i.opname in _BRANCH_OPS and None not in i.pos and i.pos not in withs \
                and not any(_inside(i.pos, a) for a in asserts):
            out.setdefault((i.idx, i.pos, i.offset if i.pos in bools else 0), []).append(i)
    return {k: tuple(v) for k, v in out.items()}


def _source(text: str, path: str) -> Optional[_Source]:
    try:
        tree = ast.parse(text)
        code = compile(text, path, "exec", dont_inherit=True)
    except (SyntaxError, ValueError):
        return None
    codes = code_tree(code)
    insts, at = _instructions(codes)
    parents = {id(c): n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    owner = _owners(tree, codes, at)
    stmts = []

    def walk(node, depth):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.stmt):
                first = min([child.lineno] + [d.lineno for d in getattr(child, "decorator_list", ())])
                stmts.append((first, child.end_lineno, child.col_offset, depth, child))
            walk(child, depth + 1)
    walk(tree, 0)
    return _Source(frozenset(at), tuple(stmts), tuple(_source_lines(text)), tree, at, owner,
                   _loops(tree, insts, parents, owner, codes), _branch_groups(tree, insts), parents)


def _within(src: _Source, node) -> frozenset:
    """{(code, row)} of the instructions inside `node`'s source."""
    return frozenset((idx, r) for r in range(node.lineno, node.end_lineno + 1)
                     for idx, ins in src.at.get(r, {}).items() if any(_inside(i.pos, node) for i in ins))


def _contains(outer, inner) -> bool:
    return (outer.lineno, outer.col_offset) <= (inner.lineno, inner.col_offset) and \
        (inner.end_lineno, inner.end_col_offset) <= (outer.end_lineno, outer.end_col_offset)


def _innermost_loop(src: _Source, i: _Inst) -> Optional[_Loop]:
    """The innermost loop whose body holds instruction `i`."""
    found = [lp for lp in src.loops if lp.idx == i.idx and any(_inside(i.pos, b) for b in lp.body)]
    return min(found, key=lambda lp: (_span(lp.node)[2] - _span(lp.node)[0], -lp.ident), default=None)


def _nested(inner: _Loop, outer: _Loop) -> bool:
    if inner.idx != outer.idx or inner is outer:
        return False
    if inner.node is outer.node:
        return inner.ident > outer.ident
    return any(_contains(b, inner.node) for b in outer.body) or (
        outer.body == () and _contains(outer.node, inner.node))


class _Watch(NamedTuple):
    """What a recording watches of a file's changed rows (`watched`), and
    what the gate needs of them: `groups` [(code, position, (_Inst, ...))]
    the branches on them; `probes` [(code, row, loop, offset)] each row's
    first instruction in each loop body it is in; `loops` [(code, loop,
    back-edges, loops inside it)] each probed loop and each around one;
    `defaults` [(code, parameter, literal (repr) or None, row, node)] each
    default whose expression is on one."""
    groups: list
    probes: list
    loops: list
    defaults: list


def watched(src: _Source, rows) -> _Watch:
    rows = set(rows)
    groups = [(k[0], k[1], v) for k, v in sorted(src.groups.items()) if k[1][0] in rows]
    probes = []
    for row in sorted(rows):
        for idx, ins in sorted(src.at.get(row, {}).items()):
            for i in ins:
                loop = _innermost_loop(src, i)
                if loop is not None:
                    probes.append((idx, row, loop.ident, i.offset))
    probed = {(idx, ident) for idx, _row, ident, _at in probes}
    loops = []
    for lp in src.loops:
        inner = tuple(x.ident for x in src.loops if _nested(x, lp))
        if (lp.idx, lp.ident) in probed or any((lp.idx, x) in probed for x in inner):
            loops.append((lp.idx, lp.ident, lp.backs, inner))
    defaults = []
    for node in ast.walk(src.tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        a = node.args
        positional = a.posonlyargs + a.args
        pairs = list(zip(positional[len(positional) - len(a.defaults):], a.defaults))
        pairs += [(arg, d) for arg, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None]
        for arg, d in pairs:
            hit = [r for r in range(d.lineno, d.end_lineno + 1) if r in rows]
            if not hit:
                continue
            try:
                lit = repr(ast.literal_eval(d))
            except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
                lit = None
            defaults.append((src.owner.get(id(node), -1), arg.arg, lit, hit[0], node))
    return _Watch(groups, probes, loops, defaults)


def reach_spec(this: dict, other: dict, root: Path, side: str) -> list:
    """What a recording of `this` (src_files of `root`) traced against
    `other` as the comparison's `side` ("HEAD": `other` is BASE; "BASE":
    it is HEAD) watches, as REACH_FILE rows the network shim reads: `P
    PATH` a .py file traced (its lines, its code objects entered), `N
    PATH` a file not Python (its opens); of each changed row of a .py
    file (its side of `diff_rows`, as the gate reads it), `B PATH CODE
    OFFSETS` the branches on it, `R PATH CODE ROW LOOP OFFSET` a row in a
    loop's body and where it is probed, `J PATH CODE LOOP BACK-EDGES
    INNER` each loop that holds one, and `D PATH CODE PARAMETER LITERAL` a
    default whose expression is on it."""
    rows = []
    top = os.path.realpath(root / SRC)
    for rel in traced_files(other, this):
        path = os.path.join(top, rel)
        if not rel.endswith(".py"):
            rows.append(f"N\t{path}")
            continue
        rows.append(f"P\t{path}")
        text, them = _decoded(this[rel]), _decoded(other.get(rel))
        d = diff_rows(text, them) if side == "BASE" else diff_rows(them, text)
        own = d.base if side == "BASE" else d.head
        src = _source(text, rel)
        if src is None or not own:
            continue
        w = watched(src, own)
        branches = {}
        for idx, _pos, ins in w.groups:
            branches.setdefault(idx, set()).update(i.offset for i in ins)
        rows += [f"B\t{path}\t{idx}\t{','.join(map(str, sorted(offs)))}" for idx, offs in sorted(branches.items())]
        rows += [f"J\t{path}\t{idx}\t{loop}\t{','.join(map(str, backs))}\t{','.join(map(str, inner))}"
                 for idx, loop, backs, inner in w.loops]
        rows += [f"R\t{path}\t{idx}\t{row}\t{loop}\t{at}" for idx, row, loop, at in w.probes]
        rows += [f"D\t{path}\t{idx}\t{param}\t{lit}" for idx, param, lit, _row, _node in w.defaults
                 if lit is not None and idx >= 0]
    return rows


def _header_end(node) -> int:
    """The last line of a compound statement's header: the line before its
    first inner statement (its own line when that is on it)."""
    inner = [n.lineno for n in ast.walk(node) if isinstance(n, ast.stmt) and n is not node]
    return max(node.lineno, min(inner) - 1) if inner else node.end_lineno


def _clause_body(src: _Source, row: int):
    """The first statement of the body a clause keyword line (`else:`,
    `finally:`) opens, or None."""
    for node in ast.walk(src.tree):
        parts = []
        if isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While)):
            parts.append((node.body, node.orelse))
        elif isinstance(node, (ast.Try, ast.TryStar)):
            parts.append(((node.handlers or node.body), node.orelse))
            parts.append(((node.orelse or node.handlers or node.body), node.finalbody))
        for before, section in parts:
            if section and before and before[-1].end_lineno < row < section[0].lineno:
                return section[0]
    return None


def _branch_keys(groups) -> frozenset:
    return frozenset((idx, i.offset, i.nxt, _BRANCH_OPS[i.opname]) for idx, _pos, ins in groups for i in ins)


def requirements(path: str, text: str, rows, gaps=(), side: str = "HEAD") -> list:
    """The Needs of a changed Python file (`diff_rows`' rows of its side,
    and HEAD's gaps): a changed line on which an instruction is needs it
    run in each code object it is in (a lambda on it called, not just the
    line around it), in two iterations of one entry of each loop whose
    body it is in, and each branch on it taken both ways; a line with
    none (inside a statement spanning lines, a docstring) needs the
    innermost part of its statement that has one run, and each
    conditional around that part (a conditional expression, and/or, a
    comprehension: its branches both ways; a lambda: called); a clause
    keyword line (`else:`, `finally:`) the first statement of its body;
    a function's header needs it called, and each default on a changed
    line used by a call; a deletion needs the statement beside it in its
    block (the one before it at its indent, else the one after) run; each
    function whose body changed needs to have been called (its def
    line)."""
    src = _source(text, path)
    if src is None:
        return [Need(3, path, r, "", "module", why="does not compile", side=side) for r in rows or [1]]
    needs, funcs = [], {}

    def text_of(row):
        return src.lines[row - 1] if 0 < row <= len(src.lines) else ""

    def need(order, row, kind, keys=(), why=""):
        return Need(order, path, row, text_of(row), kind, frozenset(keys), why, side)

    def innermost(row, spans=None):
        found = [s for s in src.stmts if s[0] <= row <= s[1] and (spans is None or spans(s))]
        return max(found, key=lambda s: (s[3], s[0]), default=None)

    def func(row):
        return innermost(row, lambda s: isinstance(s[4], _DEFS))

    def entered(node, order, row, why):
        idx = src.owner.get(id(node))
        if idx is None:
            return need(3, row, "module", (), why + " (its module imported)")
        return need(order, row, "entered", {idx}, why)

    def ran(s, order, row, why, fallback_row):
        """What statement `s` having run needs (its lines, else the function
        it is in, else the module)."""
        node = s[4]
        if isinstance(node, _DEFS + (ast.ClassDef,)):
            span = range(s[0], node.lineno + 1)
        elif isinstance(node, _SIMPLE):
            span = range(s[0], s[1] + 1)
        else:
            span = range(s[0], _header_end(node) + 1)
        hit = frozenset((idx, r) for r in span for idx in src.at.get(r, ()))
        if hit:
            return need(order, row, "rows", hit, why)
        f = func(fallback_row)
        if f is not None and f is not s:
            return entered(f[4], order, row, why + " (no instruction of its own: its function called)")
        return need(3, row, "module", (), why + " (its module imported)")

    def body_of(f, row):
        if f is not None and row > _header_end(f[4]):
            funcs.setdefault(f[0], entered(f[4], 2, f[4].lineno, "a function whose body changed, never called"))

    def conditionals(node, row):
        """The needs of each conditional around `node`, inside its statement."""
        out, cur = [], src.parents.get(id(node))
        while cur is not None and not isinstance(cur, ast.stmt):
            if isinstance(cur, (ast.Lambda, ast.GeneratorExp)):
                out.append(entered(cur, 0, row, "a changed line in a lambda or generator never run"))
            if isinstance(cur, _CONDITIONALS) and not isinstance(cur, ast.Lambda):
                tests = ([cur.test] if isinstance(cur, ast.IfExp) else cur.values[:-1]
                         if isinstance(cur, ast.BoolOp) else [x for g in cur.generators for x in g.ifs])
                groups = [(k[0], k[1], v) for k, v in src.groups.items()
                          if any(_inside(k[1], t) for t in tests) or (isinstance(cur, _COMPS) and k[1] == _span(cur))]
                if groups:
                    out.append(need(0, row, "branch", _branch_keys(groups),
                                    "a changed line in an arm or operand: the test before it not taken both ways"))
            cur = src.parents.get(id(cur))
        return out

    for row in rows:
        f = func(row)
        if f is not None and row <= _header_end(f[4]):
            needs.append(entered(f[4], 0, row, "a changed function header, its function never called"))
            continue
        body_of(f, row)
        here = src.at.get(row)
        if here:
            for idx in sorted(here):
                needs.append(need(0, row, "line", {(idx, row)}, "a changed line" + (
                    "" if idx == min(here) else ": a lambda or generator on it never run")))
            continue
        first = _clause_body(src, row)
        if first is not None:
            s = next(x for x in src.stmts if x[4] is first)
            needs.append(ran(s, 0, row, "a changed clause line, the first statement of its body never run", row))
            continue
        nodes = sorted((n for n in ast.walk(src.tree) if hasattr(n, "lineno") and not isinstance(n, ast.stmt)
                        and n.lineno <= row <= n.end_lineno),
                       key=lambda n: (n.end_lineno - n.lineno, n.end_col_offset - n.col_offset))
        part = next((n for n in nodes if not isinstance(n, _SCOPES) and _within(src, n)), None)
        s = innermost(row)
        if part is not None and (s is None or _contains(s[4], part)):
            needs.append(need(0, row, "rows", _within(src, part),
                              "a changed line of a statement, the part of it there never run"))
            needs += conditionals(part, row)
            continue
        if s is None:
            needs.append(need(3, row, "module", (), "a changed line, its module never imported"))
            continue
        if isinstance(s[4], _SIMPLE) or row <= _header_end(s[4]):
            needs.append(ran(s, 0, row, "a changed line of a statement", row))
        elif f is not None:
            needs.append(entered(f[4], 0, row, "a changed line between blocks, its function never called"))
        else:
            needs.append(need(3, row, "module", (), "a changed line, its module never imported"))
    w = watched(src, rows)
    for idx, pos, ins in w.groups:
        needs.append(need(0, pos[0], "branch", _branch_keys([(idx, pos, ins)]),
                          "a changed branch not taken both ways"))
    for idx, row, loop, _at in w.probes:
        needs.append(need(0, row, "loop", {(idx, row, loop)},
                          "a changed line in a loop, never run in two iterations of one entry of it"))
    for idx, param, lit, row, _node in w.defaults:
        if lit is None or idx < 0:
            needs.append(need(0, row, "never", (), f"a changed default (`{param}`) that is not a literal: "
                                                   "whether a call used it is not traced"))
        else:
            needs.append(need(0, row, "default", {(idx, param)},
                              f"a changed default (`{param}`) no call used"))
    for g, deleted in gaps:
        indent = len(deleted) - len(deleted.lstrip())
        before = max((s for s in src.stmts if s[1] <= g and s[2] <= indent), key=lambda s: (s[1], s[3]),
                     default=None)
        after = min((s for s in src.stmts if s[0] >= g + 1 and s[2] <= indent), key=lambda s: (s[0], -s[3]),
                    default=None)
        side_s = before if before and before[2] == indent else after if after and after[2] == indent else None
        what = f"lines deleted beside it (`{deleted.strip()[:60]}`)"
        if side_s is None:
            side_s = innermost(g, lambda s: s[1] >= g + 1) or innermost(g + 1)
        if side_s is None:
            needs.append(need(1, max(g, 1), "module", (), what))
            continue
        needs.append(ran(side_s, 1, side_s[0], what, side_s[0]))
        f = innermost(g, lambda s: isinstance(s[4], _DEFS) and s[1] >= g + 1)
        body_of(f, g + 1)
    return needs + list(funcs.values())


# A string constant's format fields and conversions: where its fragments
# split (`_fragments`).
_FIELD = re.compile(r"\{[^{}]*\}|%(?:\([^)]*\))?[-#0 +]*(?:\*|\d+)?(?:\.(?:\*|\d+))?[hlL]?[diouxXeEfFgGcrsa%]")
# Where a changed line of a file that is not Python splits into fragments.
_DATA_SPLIT = re.compile(r"[=,;\"'\[\]{}#|\t]")
TEXT_MIN = 4
_BLANKS = re.compile(rb"\s+")
_HYPHEN = re.compile(rb"- ")
# A call whose first (else second) string argument names a key, a test or
# a codec, never text shown.
_KEY_CALLS = frozenset(("get", "pop", "setdefault", "startswith", "endswith", "getenv"))
_NAME_CALLS = frozenset(("getattr", "hasattr", "setattr", "delattr"))
_CODEC_KEYWORDS = frozenset(("encoding", "errors", "newline"))


def _squeezed(text: str) -> str:
    return _HYPHEN.sub(b"-", _BLANKS.sub(b" ", text.encode("utf-8", "surrogateescape"))).decode(
        "utf-8", "surrogateescape").strip()


def _fragments(text: str, split=_FIELD) -> list:
    """`text`'s fragments: its pieces between format fields (or, for a
    file not Python, its separators), blanks squeezed, each of at least
    TEXT_MIN characters but blanks."""
    out = []
    for piece in split.split(text):
        piece = _squeezed(piece)
        if len(piece.replace(" ", "")) >= TEXT_MIN:
            out.append(piece)
    return out


def _doc_readers(files: dict) -> tuple:
    """(whether src/taxjson reads a docstring of another object: a
    `.__doc__`, getdoc or help; {path of a module that reads its own
    `__doc__`})."""
    attr, own = False, set()
    for rel, data in files.items():
        if not rel.endswith(".py") or not any(w in data for w in (b"__doc__", b"getdoc", b"help(")):
            continue
        try:
            tree = ast.parse(_decoded(data))
        except (SyntaxError, ValueError):
            attr = True
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Attribute) and n.attr == "__doc__":
                attr = True
            elif isinstance(n, ast.Name) and n.id == "__doc__":
                own.add(rel)
            elif isinstance(n, ast.Call) and (getattr(n.func, "id", None) in ("getdoc", "help")
                                              or getattr(n.func, "attr", None) in ("getdoc", "help")):
                attr = True
    return attr, own


def _shown_text(node, parents: dict, docs: tuple, path: str) -> bool:
    """Whether a string constant can be shown itself: not a key (`x["k"]`,
    `.get("k")`, getattr's name), a test's operand, a codec's name, an
    annotation, `__all__`'s, a bare string statement, a docstring no code
    reads."""
    child, cur = node, parents.get(id(node))
    if isinstance(cur, ast.Tuple):
        child, cur = cur, parents.get(id(cur))
    if isinstance(cur, ast.Subscript) and child is cur.slice:
        return False
    if isinstance(cur, (ast.Compare, ast.MatchValue)):
        return False
    if isinstance(cur, ast.keyword) and cur.arg in _CODEC_KEYWORDS:
        return False
    if isinstance(cur, ast.Call) and child in cur.args:
        name = getattr(cur.func, "attr", None) or getattr(cur.func, "id", None)
        at = cur.args.index(child)
        if (name in _KEY_CALLS and at == 0) or (name in _NAME_CALLS and at == 1):
            return False
    if isinstance(cur, ast.Expr):
        scope = parents.get(id(cur))
        if isinstance(scope, (ast.Module,) + _DEFS + (ast.ClassDef,)) and scope.body and scope.body[0] is cur:
            return docs[0] or (isinstance(scope, ast.Module) and path in docs[1])
        return False
    walk = node
    while walk is not None:
        up = parents.get(id(walk))
        if isinstance(up, ast.arg) and walk is up.annotation or isinstance(up, _DEFS) and walk is up.returns \
                or isinstance(up, ast.AnnAssign) and walk is up.annotation:
            return False
        if isinstance(up, ast.Assign) and any(getattr(t, "id", None) == "__all__" for t in up.targets):
            return False
        walk = up
    return True


def text_requirements(path: str, text: str, rows, docs: tuple) -> list:
    """The text Needs of a changed Python file of HEAD: each string
    constant (an f-string's literal parts) any part of which is on a
    changed row and that can be shown itself (`_shown_text`) needs each of
    its fragments (`_fragments`) in the recording's output."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    lines, rows = _source_lines(text), set(rows)
    parents = {id(c): n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    needs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr) and not isinstance(parents.get(id(node)), ast.FormattedValue):
            parts = "".join(v.value if isinstance(v, ast.Constant) else "\0" for v in node.values)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and \
                not isinstance(parents.get(id(node)), (ast.JoinedStr, ast.FormattedValue)):
            parts = node.value
        else:
            continue
        hit = [r for r in range(node.lineno, node.end_lineno + 1) if r in rows]
        if not hit or not _shown_text(node, parents, docs, path):
            continue
        frags = [f for chunk in parts.split("\0") for f in _fragments(chunk)]
        if frags:
            needs.append(Need(0, path, hit[0], lines[hit[0] - 1] if hit[0] <= len(lines) else "", "text",
                              frozenset(frags), "a changed text no recorded output shows"))
    return needs


def data_requirements(path: str, rows, gaps, text: str, side: str = "HEAD") -> list:
    """A changed file of src/taxjson that is not Python (a layout, a demo
    export): no line of it is traced, so each changed line needs the file
    opened by a recorded command, and in HEAD its fragments (split at its
    separators, `_DATA_SPLIT`) in the recording's output."""
    lines = _source_lines(text)
    needs = []
    for r in list(rows) + [max(g, 1) for g, _d in gaps]:
        shown = lines[r - 1] if r <= len(lines) else ""
        needs.append(Need(0, path, r, shown, "opened", why="a changed line of a file no recorded command opened",
                          side=side))
        frags = _fragments(shown, _DATA_SPLIT) if side == "HEAD" and r in rows else []
        if frags:
            needs.append(Need(0, path, r, shown, "text", frozenset(frags),
                              "a changed line of a file no recorded output shows", side))
    return needs


def _chains(text: str) -> list:
    """[(statement or call as ast.unparse writes it, the try, finally and
    with chain around it, its first row, its own chain)] of each simple
    statement and call: its own chain is each enclosing try (its handlers,
    and whether it has a finally) by the part of it the code is in, and
    each with, out to its function; the chain, that one with, for a
    function of the module called from one place, that place's chain
    around it (from several, all of theirs)."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return []
    parents = {id(c): n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    sites = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            name = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
            if name:
                sites.setdefault(name, []).append(n)

    def chain(node, depth):
        out, child, cur = [], node, parents.get(id(node))
        while cur is not None and not isinstance(cur, _SCOPES + (ast.Module,)):
            if isinstance(cur, (ast.Try, ast.TryStar)):
                handlers = tuple(ast.unparse(h.type) if h.type else "*" for h in cur.handlers)
                if child in cur.body:
                    out.append(("try", handlers, bool(cur.finalbody)))
                elif child in cur.orelse:
                    out.append(("try else", bool(cur.finalbody)))
                elif child in cur.finalbody:
                    out.append(("finally",))
                else:
                    out.append(("except", ast.unparse(child.type) if child.type else "*", bool(cur.finalbody)))
            elif isinstance(cur, (ast.With, ast.AsyncWith)) and child in cur.body:
                out.append(("with", tuple(ast.unparse(i) for i in cur.items)))
            child, cur = cur, parents.get(id(cur))
        own = tuple(reversed(out))
        if isinstance(cur, _DEFS) and depth < 4:
            outer = {chain(s, depth + 1) for s in sites.get(cur.name, ())}
            if len(outer) == 1:
                own = next(iter(outer)) + own
            elif outer:
                own = (("called from", tuple(sorted(map(repr, outer)))),) + own
        return own
    return [(ast.unparse(n), chain(n, 0), n.lineno, chain(n, 4)) for n in ast.walk(tree)
            if isinstance(n, _SIMPLE + (ast.Call,))]


def _said_chain(ch) -> str:
    """A chain of `_chains` as the gate says it, outermost first: `try
    ValueError (and a finally) > with open(p)`."""
    def one(c):
        fin = " (and a finally)" if c[-1] is True else ""
        if c[0] == "try":
            return " ".join(["try"] + ([", ".join(c[1])] if c[1] else [])) + fin
        if c[0] == "except":
            return f"except {c[1]}{fin}"
        if c[0] == "with":
            return "with " + ", ".join(c[1])
        return c[0] + fin
    return " > ".join(one(c) for c in ch) or "none"


def context_requirements(path: str, base: str, head: str, d: Diff) -> list:
    """TJ-4632: each statement or call keeps the chain of tries, finallys
    and withs around it (`_chains`): on a line the diff leaves, its own
    chain as on its line in BASE (an `else:` made `finally:` around it; a
    call site's chain changed is said at the call, not on each line of
    what it calls); one the diff adds or changes in HEAD, by its text, the
    chain one BASE deletes or replaces had, its callers' around it (a
    statement moved out of its try; a helper called inside the try
    passes). Each HEAD occurrence
    beyond BASE's in its chain needs what no recording shows (it moved into
    or out of a try, a finally or a with: an exception now caught by
    another handler, or by none)."""
    same = {}
    for tag, i1, i2, j1, _j2 in difflib.SequenceMatcher(None, _normalized(base), _normalized(head),
                                                         autojunk=False).get_opcodes():
        if tag == "equal":
            same.update((i1 + k + 1, j1 + k + 1) for k in range(i2 - i1))
    removed, added = set(d.base), set(d.head)
    was, kept = {}, {}
    for t, ch, r, own in _chains(base):
        if r in removed:
            was.setdefault(t, []).append(ch)
        elif r in same:
            kept.setdefault((same[r], t), []).append(own)
    lines = _source_lines(head)
    needs, now = [], {}

    def moved(t, r, before, ch):
        needs.append(Need(0, path, r, lines[r - 1] if r <= len(lines) else "", "never", why=(
            f"`{t[:50]}` moved into or out of a try, a finally or a with (BASE: "
            f"{'; '.join(sorted({_said_chain(c) for c in before})) or 'none'}; HEAD: {_said_chain(ch)})")))
    for t, ch, r, own in _chains(head):
        if r in added:
            now.setdefault(t, []).append((ch, r))
        elif (r, t) in kept:
            left = kept[(r, t)]
            if own in left:
                left.remove(own)
            else:
                moved(t, r, list(left), own)
    for t, occ in sorted(now.items()):
        if t not in was:
            continue
        left, rest = list(was[t]), []
        for ch, r in sorted(occ, key=lambda o: o[1]):
            if ch in left:
                left.remove(ch)
            else:
                rest.append((ch, r))
        for ch, r in rest:
            # A copy beyond BASE's count, in a chain BASE had it in, is new
            # code the line rules hold; one in a chain it never had, or
            # while a BASE occurrence is still unmatched, moved.
            if left or ch not in was[t]:
                moved(t, r, left or was[t], ch)
    return needs


class _Ran(NamedTuple):
    lines: set
    entered: set
    dests: dict
    loops: set
    defaults: set
    opened: set
    files: set


def _ran(reach: dict) -> _Ran:
    """What a recording's `reach` says ran, by path below src/taxjson."""
    lines = {(p, idx, r) for p, v in (reach.get("lines") or {}).items() for idx, r in v}
    dests = {}
    for p, v in (reach.get("branches") or {}).items():
        for idx, at, dest in v:
            dests.setdefault((p, idx, at), set()).add(dest)
    return _Ran(lines, {(p, idx) for p, v in (reach.get("entered") or {}).items() for idx in v}, dests,
                {(p, idx, r, lp) for p, v in (reach.get("loops") or {}).items() for idx, r, lp in v},
                {(p, idx, a) for p, v in (reach.get("defaults") or {}).items() for idx, a in v},
                set(reach.get("opened") or ()), {p for p, _i, _r in lines})


def satisfied(need: Need, ran: _Ran, shown=frozenset()) -> bool:
    """Whether a recording's trace (`_ran`) and output (`shown`: the text
    fragments it holds) saw what `need` needs."""
    p = need.path
    if need.kind in ("line", "rows"):
        return any((p, idx, r) in ran.lines for idx, r in need.keys)
    if need.kind == "entered":
        return any((p, idx) in ran.entered for idx in need.keys)
    if need.kind == "module":
        return p in ran.files or (p, 0) in ran.entered
    if need.kind == "branch":
        seen = {fell if dest == nxt else not fell for idx, at, nxt, fell in need.keys
                for dest in ran.dests.get((p, idx, at), ())}
        return seen == {True, False}
    if need.kind == "loop":
        return all((p, *k) in ran.loops for k in need.keys)
    if need.kind == "default":
        return all((p, *k) in ran.defaults for k in need.keys)
    if need.kind == "text":
        return need.keys <= shown
    return need.kind == "opened" and p in ran.opened


def read_unreachable(tree, files: dict) -> tuple:
    """({(side, path below src/taxjson, row)}, [problem]) of `tree`'s
    UNREACHABLE: `src/taxjson/PATH:LINE :: TEXT :: REASON` a line of HEAD,
    `BASE src/taxjson/PATH:LINE :: ...` a line of BASE the diff deletes or
    replaces, `#` a comment; `files` {side: src_files}. An entry excuses
    the line it names, and only while TEXT is that line's (blanks at its
    ends aside); one whose TEXT is not, or with no reason, is a problem."""
    data = _tree_file(tree, UNREACHABLE)
    entries, problems = set(), []
    for n, raw in enumerate((data or b"").decode("utf-8", "replace").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        where, sep, rest = raw.partition(" :: ")
        text, sep2, reason = rest.rpartition(" :: ")
        prefix, _sp, where = where.strip().rpartition(" ")
        side = "BASE" if prefix == "BASE" else "HEAD"
        path, colon, row = where.rpartition(":")
        rel = path[len(SRC) + 1:] if path.startswith(SRC + "/") else None
        if not (sep and sep2 and rel and colon and row.isdigit() and prefix in ("", "BASE") and text.strip()
                and reason.strip()):
            problems.append(f"{UNREACHABLE}:{n}: not `[BASE ]{SRC}/PATH:LINE :: TEXT :: REASON`")
            continue
        lines = _source_lines(_decoded(files.get(side, {}).get(rel)))
        at = int(row)
        if not (0 < at <= len(lines) and lines[at - 1].strip() == text.strip()):
            problems.append(f"{UNREACHABLE}:{n}: `{text.strip()[:80]}` is not line {at} of {SRC}/{rel}"
                            + (" in BASE" if side == "BASE" else ""))
            continue
        entries.add((side, rel, at))
    return entries, problems


def _shown(root: Optional[Path], fragments) -> frozenset:
    """The fragments that some file of recording `root` holds (its outputs,
    its files and its .meta; blanks squeezed as `_fragments` squeezes them;
    as written, as JSON escapes it, and as `masked` doubles a `<`)."""
    want = {}
    for f in fragments:
        forms = {f, json.dumps(f)[1:-1]}
        want[f] = {x.encode("utf-8", "surrogateescape") for x in forms | {x.replace("<", "<<") for x in forms}}
    found = set()
    for p in (_files(root).values() if root is not None and want else ()):
        if p.name == "passes":
            continue
        data = _HYPHEN.sub(b"-", _BLANKS.sub(b" ", p.read_bytes()))
        for f in [f for f in want if any(x in data for x in want[f])]:
            found.add(f)
            del want[f]
        if not want:
            break
    return frozenset(found)


def file_requirements(rel: str, base, head, docs: tuple) -> list:
    """The Needs of one changed file (its BASE and HEAD bytes, either None
    when the file is not in that tree): HEAD's changed rows and BASE's
    deleted or replaced ones (`requirements`, `data_requirements`), HEAD's
    texts, the statements moved in or out of a try, and the rows whose bytes
    alone changed (their module imported)."""
    python = rel.endswith(".py")
    a, b = _decoded(base), _decoded(head)
    d = diff_rows(a, b, python)
    needs = []
    for side, data, text, rows, raw in (("HEAD", head, b, d.head, d.head_raw), ("BASE", base, a, d.base, d.base_raw)):
        if data is None:
            continue
        gaps = d.gaps if side == "HEAD" else ()
        if python:
            needs += requirements(rel, text, rows, gaps, side)
            if side == "HEAD":
                needs += text_requirements(rel, text, rows, docs)
            lines = _source_lines(text)
            needs += [Need(3, rel, r, lines[r - 1] if r <= len(lines) else "", "module",
                           why="a byte changed but no code (a comment, a blank, a line's end): its module never "
                               "imported", side=side) for r in raw]
        else:
            needs += data_requirements(rel, rows, gaps if side == "HEAD" else (), text, side)
    if python and base is not None and head is not None:
        needs += context_requirements(rel, a, b, d)
    return needs


def reach_gate(pa: dict, pb: dict, trees, dirs=None) -> tuple:
    """(exit, lines): the reach gate of recordings A (BASE's, traced with
    `--reach-head HEAD`) and B (HEAD's, `--reach-base BASE`), `trees`
    (BASE, HEAD), `dirs` the recordings' folders (B's output holds the
    texts). 2 when a tree is not the one its recording was made of, or the
    Python is not the one that traced it; REACH_REFUSED when a Need of a
    changed line (`file_requirements`) was not seen in its side's
    recording, naming each, most specific first, or UNREACHABLE has a
    problem; else 0."""
    try:
        files = [src_files(t) for t in trees]
    except SystemExit as e:
        return 2, [f"refused: {e}"]
    for label, tree, got, proof in (("BASE", trees[0], files[0], pa), ("HEAD", trees[1], files[1], pb)):
        if src_digests(got) != proof.get("src"):
            return 2, [f"refused: {label} ({tree}) is not the tree its recording was made of: its {SRC} "
                       "differs from the recording's (proof.json `src`)"]
    by_side = dict(zip(SIDES, files))
    base, head = files
    changed = sorted(r for r in set(base) | set(head) if base.get(r) != head.get(r))
    if not changed:
        return 0, [f"byte proof: reach: no byte of {SRC} changed"]
    for proof in (pa, pb):
        py = (proof.get("reach") or {}).get("python")
        if py and py != platform.python_version():
            return 2, [f"refused: the reach gate reads bytecode offsets: compare under the Python that traced the "
                       f"recordings ({py}, not {platform.python_version()})"]
    docs = _doc_readers(head)
    needs = sorted(set(n for rel in changed for n in file_requirements(rel, base.get(rel), head.get(rel), docs)),
                   key=lambda n: (n.order, n.side != "HEAD", n.path, n.row, n.kind, sorted(map(repr, n.keys))))
    traces, untraced = {}, []
    for side, proof, other, how in (("BASE", pa, head, "record BASE with `--reach-head HEAD`"),
                                    ("HEAD", pb, base, "record HEAD with `--reach-base BASE`")):
        reach = proof.get("reach") or {}
        traces[side] = _ran(reach)
        ok = reach.get("side") == side and reach.get("other") == _tree_digest(other)
        mine = by_side[side]
        for p in sorted({n.path for n in needs if n.side == side}):
            if not ok or (reach.get("traced") or {}).get(p) != _digest(mine[p]):
                untraced.append((side, p, f"{SRC}/{p}{' in BASE' if side == 'BASE' else ''}: not traced: {how}"))
    off = {(s, p) for s, p, _w in untraced}
    entries, problems = read_unreachable(trees[1], by_side)
    shown = _shown(Path(dirs[1]) if dirs else None, {f for n in needs if n.kind == "text" for f in n.keys})
    unreached = [n for n in needs if (n.side, n.path) not in off and not satisfied(n, traces[n.side], shown)
                 and (n.side, n.path, n.row) not in entries]
    excused = len({(n.side, n.path, n.row) for n in needs} & entries)
    lines_needed = len({(n.side, n.path, n.row) for n in needs})
    if not (unreached or untraced or problems):
        return 0, [f"byte proof: reach: every changed line of {SRC} ran ({lines_needed} line(s) in "
                   f"{len({n.path for n in needs})} file(s); {excused} listed in {UNREACHABLE})"]
    said = [f"byte proof: refused by the reach gate: {len({(n.side, n.path, n.row) for n in unreached})} changed "
            f"line(s) of {SRC} the recordings did not run or show as the diff needs; give each a command line "
            f"that does, or list it in {UNREACHABLE} (`[BASE ]{SRC}/PATH:LINE :: TEXT :: REASON`)"]
    said += [w for _s, _p, w in untraced]
    said += [f"{SRC}/{n.path}:{n.row}{' (BASE)' if n.side == 'BASE' else ''}: {n.why}"
             + (f" (`{sorted(n.keys - shown)[0][:60]}`)" if n.kind == "text" else "") + f": {n.text.strip()[:100]}"
             for n in unreached]
    return REACH_REFUSED, said + problems


def tmpdir_shape() -> list:
    """The length of the recording's temp folder, as given and as its real
    path: a path is labelled, but its length still moves a wrap or an
    archive's size (--compare refuses two recordings of other lengths)."""
    tmp = tempfile.gettempdir()
    return [len(tmp), len(os.path.realpath(tmp))]


# ------------------------------------------------------------- comparing
def _first_difference(a: bytes, b: bytes) -> str:
    la, lb = a.split(b"\n"), b.split(b"\n")
    for i, (x, y) in enumerate(zip(la, lb)):
        if x != y:
            return f"line {i + 1}: {x!r}, now {y!r}"
    return f"{len(la)} lines, now {len(lb)}"


_CHECKED = ("format", "instants", "tz", "file_age", "edit_age", "passes", "tty_rows", "env", "isolation",
            "python", "packages", "tool", "commands", "once", "checkout_kind", "tmpdir")


def verify(d: Path, proof: dict) -> list:
    """What in recording `d` does not match its proof.json manifest."""
    want = proof.get("manifest")
    if not isinstance(want, dict) or not want:
        return ["no manifest"]
    have = manifest(d)
    bad = [f"{n}: not in its manifest" for n in sorted(set(have) - set(want))]
    bad += [f"{n}: missing" for n in sorted(set(want) - set(have))]
    bad += [f"{n}: not the file recorded" for n in sorted(set(have) & set(want)) if have[n] != want[n]]
    return bad


def compare(a: Path, b: Path, trees=None) -> tuple:
    """(exit, lines): 0 equal, 1 not, 2 refused, 3 equal but one tree, 4
    refused by the reach gate (`reach_gate` of `trees`, (BASE, HEAD): A is
    BASE's recording, B HEAD's; the command line always gives them)."""
    try:
        pa, pb = (json.loads((d / PROOF).read_text(encoding="utf-8")) for d in (a, b))
    except (OSError, ValueError) as e:
        return 2, [f"not a recording: {e}"]
    for d, p in ((a, pa), (b, pb)):
        bad = verify(d, p)
        if bad:
            return 2, [f"refused: {d} does not match its proof.json manifest: {'; '.join(bad[:5])}"
                       + (" ..." if len(bad) > 5 else "")]
    for key in _CHECKED:
        if pa.get(key) != pb.get(key):
            return 2, [f"refused: the two recordings' {key} differ ({str(pa.get(key))[:200]!r}, "
                       f"{str(pb.get(key))[:200]!r}); record both with one tool, one Python, one "
                       "isolation and the same packages and instants, of the same commands, both "
                       "from clones or both from archives, under TMPDIRs of one length"]
    gate, gated = (0, []) if trees is None else reach_gate(pa, pb, trees, (a, b))
    if gate == 2:
        return 2, gated
    code, lines = _compare_bytes(a, b, pa, pb)
    if code == 1:
        return 1, lines + gated
    return (gate, gated + lines) if gate else (code, lines + gated)


def _compare_bytes(a: Path, b: Path, pa: dict, pb: dict) -> tuple:
    fa, fb = _files(a), _files(b)
    problems = [f"{n}: only in {a}" for n in sorted(set(fa) - set(fb))]
    problems += [f"{n}: only in {b}" for n in sorted(set(fb) - set(fa))]
    for n in sorted(set(fa) & set(fb)):
        if pa["manifest"][n] != pb["manifest"][n]:
            problems.append(f"{n}: {_first_difference(fa[n].read_bytes(), fb[n].read_bytes())}")
    ta, tb = pa.get("tree"), pb.get("tree")
    one = ta == tb and ta not in (None, "", "unknown")
    warning = [f"Warning: both recordings are of one tree ({ta}): this shows whether the proof "
               "repeats, not that a change kept every byte"] if one else []
    if not problems:
        return (3 if one else 0), [f"byte proof: equal ({len(fa)} files; {ta} and {tb})", *warning]
    more = [f"... and {len(problems) - MAX_SHOWN} more"] if len(problems) > MAX_SHOWN else []
    return 1, [f"byte proof: {len(problems)} difference(s)", *problems[:MAX_SHOWN], *more, *warning]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The A/B byte proof of a change that moves no output byte.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--record", metavar="DIR", type=Path, help="record this checkout's bytes into DIR")
    mode.add_argument("--compare", nargs=2, metavar=("A", "B"), type=Path, help="two recordings, byte for byte")
    mode.add_argument("--list-commands", action="store_true",
                      help="the tjs commands and options the commands reach, and those they do not")
    ap.add_argument("--reach-base", metavar="BASE",
                    help="with --record, this checkout HEAD: trace the src/taxjson files that differ "
                         "from BASE (a folder or a commit), for the reach gate")
    ap.add_argument("--reach-head", metavar="HEAD",
                    help="with --record, this checkout BASE: trace the src/taxjson files that differ "
                         "from HEAD (a folder or a commit), for the reach gate")
    ap.add_argument("--trees", nargs=2, metavar=("BASE", "HEAD"),
                    help="with --compare (needed): the trees A and B are recordings of (folders or "
                         "commits); every src line the diff changes must have run (`reach_gate`)")
    ap.add_argument("--argv", metavar="FILE", type=Path,
                    help="more commands, added to the defaults: `BOOK COMMAND` per line")
    ap.add_argument("--book", action="append", help="a book to record (default: every golden book)")
    ap.add_argument("--jobs", type=int, default=1, help="commands at once (default 1)")
    ap.add_argument("--at", action="append", help=f"an instant every child reads (default {', '.join(INSTANTS)})")
    args = ap.parse_args(argv)
    if args.compare:
        if not args.trees:
            ap.error("--compare needs --trees BASE HEAD: the reach gate (every changed src line ran)")
        code, lines = compare(*args.compare, trees=args.trees)
        print("\n".join(lines))
        return code
    if args.trees or ((args.reach_base or args.reach_head) and not args.record):
        ap.error("--trees goes with --compare, --reach-base and --reach-head with --record")
    if args.reach_base and args.reach_head:
        ap.error("--reach-base (this checkout is HEAD) or --reach-head (it is BASE), not both")
    instants = tuple(args.at or INSTANTS)
    for at in instants:
        try:
            epoch(at)
        except ValueError as e:
            ap.error(f"--at: {e}")
    known = _golden.books()
    books = args.book or known
    unknown = [b for b in books if b not in known]
    if unknown:
        ap.error(f"not a golden book: {', '.join(unknown)} (these: {', '.join(known)})")
    commands, once = all_commands(books, args.argv)
    if args.list_commands:
        reached, every, tools = reach(commands)
        missed = [c for c in every if c not in reached]
        opts = options_reached(commands)
        total = sum(len(o) for o in registry().values())
        details = [c for c, o in registry().items() if "--details" in o]
        neither, stale = options_unreviewed(commands)
        print(f"byte proof: the commands of {len(books)} book(s) reach {len(reached)} of {len(every)} "
              f"tjs commands\nreached: {', '.join(reached)}\nnot reached ({len(missed)}): {', '.join(missed)}"
              f"\nstand-alone tools ({len(tools)} of {len(console_scripts())}): {', '.join(tools) or 'none'}"
              f"\noptions passed: {len(opts)} of {total}; --details on "
              f"{sum((c, '--details') in opts for c in details)} of {len(details)} commands; "
              f"{sum(len(cs) for cs in commands.values())} command lines, "
              f"{sum(len(cs) for cs in once.values())} of them at the first instant only"
              f"\noptions not passed, with a reason (REVIEWED_OPTIONS): {len(REVIEWED_OPTIONS)}"
              + "".join(f"\nWarning: {c} {o}: neither passed nor in REVIEWED_OPTIONS" for c, o in neither)
              + "".join(f"\nWarning: REVIEWED_OPTIONS {c} {o}: passed, or not in the registry" for c, o in stale))
        return 0
    n = record(args.record, books, commands, jobs=args.jobs, instants=instants, once=once,
               reach_base=args.reach_base, reach_head=args.reach_head)
    print(f"byte proof: recorded {n} command(s) and the runs of {len(books)} book(s), "
          f"{len(PASSES)} passes at each of {len(instants)} instants, into {args.record}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
