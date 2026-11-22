"""The house style for human-facing output (docs/output-style.md).

One home for how taxjson prints to people: the wrap width, prose
paragraphs and `- ` lists with a hanging indent, aligned `label:  value`
blocks, tables that fit the width, and the message labels with a
headline and detail lines: shown to a person, `Info:` / `Warning:` /
`Error:` start the line (the must-act ATTENTION lines are shown as
`Warning:`, their topic capitalised), every later line of the message
flush-left, and a message of more than one line followed by one blank
line (show(): printed before whatever comes next, never at the end);
captured for a program (width 0), the GNU `<prog>: note:` /
`warning:` / `error:` bytes programs read are kept (label(), relabel()).

It builds on the two older helpers instead of replacing them:

  * lib/cli_diag — the GNU `<prog>: warning: ...` convention, exit codes,
    input/output errors. `message()` is the same convention, wrapped.
  * lib/report_fmt — `render_table` (structured cells), `fmt_money`,
    `fmt_qty`. `fit_table()` is `render_table` made to fit the width.

A message as one line (`concise_line`, `split_message`, `detail_command`)
is here too, below lib/stage_msg, which captures and rewords the stage
messages (step 8 of the architecture plan: lib's import cycle).

Nothing here touches machine output: `--json`, the files under work/ and
reports/ that stages and users parse, and the run's marker lines keep
their exact shapes (the doc lists them).
"""

import contextlib
import json
import math
import os
import re
import shutil
import sys
import textwrap
from bisect import bisect_left
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from taxjson.lib.report_fmt import fmt_money, fmt_qty, render_table

__all__ = [
    "WIDTH", "width", "wrap", "fill", "message", "emit", "note", "warn",
    "attention", "error", "fail", "fit_table", "records", "kv_lines",
    "relpath", "Doc", "lint", "Verbatim", "unwrapped", "fmt_money", "fmt_qty",
    "printable", "shown", "label", "relabel", "labelled", "exit_text",
    "LABELS", "console_lint", "CONSOLE_LINE_RE", "show", "show_blocks",
    "join_blocks", "settle", "settling_streams", "real_stream",
    "ACT", "act", "act_list", "details_hint", "CONCISE_ENV", "concise_on",
    "concise_show", "fold_summary", "truncate", "classify", "prose_lines", "act_lines",
    "split_message", "detail_command", "concise_line",
    "render", "put", "clean_json", "JSON_SPEC",
]

# Prose wraps here when stdout is not a terminal (a pipe, a file, a test).
WIDTH = 120
# On a terminal it wraps at the terminal's width, at most this.
MAX_WIDTH = 160
# Never wrap narrower than this, whatever the terminal says.
MIN_WIDTH = 40
# The hanging indent of a captured message's continuation and detail
# lines (width 0: the run's DIAGNOSTICS collector keeps a marker line
# plus the indented lines that follow it) and of a per-record table
# layout. Shown to a person, a message's later lines are flush-left.
DETAIL_INDENT = "  "


# > 0 while output is captured for a program rather than shown to a
# person (unwrapped()): a stage's stdout written to a work/ or reports/
# file, its stderr kept as the .diag the .sum DIAGNOSTICS fold in, the
# output `tjs checklist` reads. Those are never wrapped, so their
# bytes do not depend on a terminal and every line a program greps stays
# whole; whoever shows them to a person wraps them then.
_UNWRAPPED = 0


@contextlib.contextmanager
def unwrapped():
    """Within it, width() is 0 — nothing wraps (see _UNWRAPPED)."""
    global _UNWRAPPED
    _UNWRAPPED += 1
    try:
        yield
    finally:
        _UNWRAPPED -= 1


def width(stream=None) -> int:
    """The wrap width for `stream` (stdout by default): 0 (never wrap)
    inside unwrapped(); TAXJSON_WIDTH when set (0 = never wrap); else
    the terminal's width on a terminal (at most MAX_WIDTH, 160; at
    least MIN_WIDTH, 40), else WIDTH (120)."""
    if _UNWRAPPED:
        return 0
    env = os.environ.get("TAXJSON_WIDTH", "").strip()
    if env:
        try:
            n = int(env)
        except ValueError:
            n = -1
        if n == 0:
            return 0
        if n > 0:
            return max(n, MIN_WIDTH)
    stream = sys.stdout if stream is None else stream
    try:
        tty = stream.isatty()
    except (AttributeError, ValueError, OSError):
        tty = False
    if not tty:
        return WIDTH
    cols = shutil.get_terminal_size((WIDTH, 24)).columns
    return max(MIN_WIDTH, min(cols, MAX_WIDTH))


# Control characters other than newline and tab: C0, DEL and C1. Text
# shown to a person (width > 0) carries each as a visible `\xNN`, so a
# symbol or description from a broker export cannot drive the terminal
# (an ESC sequence, a BEL, a carriage return that overprints a line).
# Captured output (width 0: work/, reports/, a .diag) keeps its bytes.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def printable(text) -> str:
    r"""`text` with every control character but newline and tab shown as
    the four characters `\xNN` (ESC -> `\x1b`). Idempotent."""
    text = str(text)
    if not _CONTROL_RE.search(text):
        return text
    return _CONTROL_RE.sub(lambda m: "\\x%02x" % ord(m.group(0)), text)


# Every control character, newline and tab included.
_ANY_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_NAMED_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def one_line(text) -> str:
    r"""`text` with EVERY control character escaped — newline, carriage
    return and tab as `\n` `\r` `\t`, the rest as `\xNN` — so a file
    name (which may hold any of them) is one line wherever it is shown
    or written: on the console, in a .diag line a program reads back.
    Idempotent on its own output."""
    text = str(text)
    if not _ANY_CONTROL_RE.search(text):
        return text
    return _ANY_CONTROL_RE.sub(
        lambda m: _NAMED_ESCAPES.get(m.group(0),
                                     "\\x%02x" % ord(m.group(0))), text)


def shown(text, stream=None) -> str:
    """`text` as printed to `stream` (stdout by default): printable()
    when it is shown to a person (width > 0), as is when captured."""
    return printable(text) if width(stream) > 0 else str(text)


# A `code span` (a command to copy, a key=value, a path) is never broken
# across lines: its spaces become non-breaking while wrapping.
_CODE_RE = re.compile(r"`[^`\n]+`")
_NBSP = " "


def wrap(text: str, width_: Optional[int] = None, indent: str = "",
         hang: Optional[str] = None, stream=None) -> List[str]:
    """`text` as lines of at most `width_` columns (default: width()),
    the first starting with `indent`, the rest with `hang` (default:
    `indent`). Words, hyphenated ids and `code spans` are never broken;
    a single word longer than the line stands on its own line. Width 0:
    one line. Existing newlines start new lines (each wrapped alike).
    Wrapped for a person (width > 0), control characters are shown
    escaped (printable()); width 0 keeps the text's bytes."""
    w = width(stream) if width_ is None else width_
    hang = indent if hang is None else hang
    if w > 0:
        text = printable(text)
        indent, hang = printable(indent), printable(hang)
    out: List[str] = []
    first = True
    for part in str(text).split("\n"):
        own = part[:len(part) - len(part.lstrip())]
        # A later line that brings its own indentation (a message's
        # "\n  - item" lines) keeps it; a bare one hangs.
        lead = indent if first else (own or hang)
        hang_part = hang if first or not own else (
            own + ("  " if part.lstrip().startswith("- ") else ""))
        first = False
        if not part.strip():
            out.append("")
            continue
        protected = _CODE_RE.sub(lambda m: m.group(0).replace(" ", _NBSP),
                                 part)
        if w <= 0:
            lines = [lead + protected.strip()]
        else:
            lines = textwrap.wrap(
                protected.strip(), width=max(w, len(lead) + 20),
                initial_indent=lead, subsequent_indent=hang_part,
                break_long_words=False, break_on_hyphens=False) or [lead]
        out.extend(ln.replace(_NBSP, " ") for ln in lines)
    return out


def fill(text: str, width_: Optional[int] = None, indent: str = "",
         hang: Optional[str] = None, stream=None) -> str:
    """wrap() joined with newlines."""
    return "\n".join(wrap(text, width_, indent, hang, stream))


# ------------------------------------------------------------ messages
# The label of each message kind, in its two forms. This is the one place
# the choice is made (docs/output-style.md, Messages):
#   * captured for a program (width 0: a work/ or reports/ file, a .diag
#     the .sum DIAGNOSTICS fold in, the text `tjs checklist` and the
#     run read back): the GNU `[<prog>: ]<kind>: ` form, lower case —
#     those bytes never change;
#   * shown to a person (width > 0): the capitalised label starts the
#     line, with no program name in front of it.
_KINDS = {
    "note": "note: ",
    "warning": "warning: ",
    "attention": "warning: ATTENTION: ",
    "error": "error: ",
}
LABELS = {
    "note": "Info: ",
    "warning": "Warning: ",
    # The must-act channel is a Warning to a person: the ATTENTION word
    # stays in the captured bytes (`warning: ATTENTION: <topic>: ...`,
    # what the run and the .sum read back), the topic is shown
    # capitalised (shown_topic).
    "attention": "Warning: ",
    "error": "Error: ",
}

# The ATTENTION topics as a person is shown them (after `Warning: `):
# the captured topic word is lower case and reads like a second label
# (`Warning: short: ...`). Display only; a topic not listed keeps its
# spelling.
ATTENTION_TOPICS = {
    "short": "Short position",
    "income year": "Income year",
    "opening": "Opening snapshot",
    "crypto id": "Crypto id",
    "split": "Split",
    "own-account move": "Move between your accounts",
    "more assignments than writes": "More assignments than writes",
    "generic importer": "Generic importer",
    "schema": "Row check",
    "dedup": "Duplicates",
    "transfer-in": "Transfer-in",
    "unapplied basis adjustment": "Unapplied basis adjustment",
    "wash sale reaches a filed year": "Wash sale reaches a filed year",
}
_TOPIC_RE = re.compile(r"(?P<topic>[a-z][a-z -]*?): ")

# A parser's warning marked for `tjs run`'s end-of-run list, after its
# `warning: ATTENTION: `: the run reads it back from the parser's .diag
# (lib/first_run.parser_warnings). In the captured bytes only: a person
# is shown the warning without it (shown_topic). Only the owner's SAY
# rulings carry it (backlog TJ-4559 (1), TJ-4569 (3); the others: TJ-4730),
# a generic mapping never run on a real export (TJ-2573), and a
# Questrade file's rows that differ only by a blank Symbol (TJ-4955).
END_OF_RUN_MARK = "[end of run] "


def unmarked(line: str) -> str:
    """A captured line without END_OF_RUN_MARK: what the console (any
    width) and a report (the .sum DIAGNOSTICS) copy of a .diag line —
    the mark is for lib/first_run alone (review round 1: it reached the
    .sum and the width-0 console)."""
    return line.replace("ATTENTION: " + END_OF_RUN_MARK, "ATTENTION: ", 1)


def shown_topic(text: str) -> str:
    """An ATTENTION message's text as a person is shown it, after its
    `Warning: ` label: the `ATTENTION: ` word dropped, a known topic
    capitalised (ATTENTION_TOPICS). Display only."""
    if text.startswith("ATTENTION: "):
        text = text[len("ATTENTION: "):]
    text = text.removeprefix(END_OF_RUN_MARK)   # (unmarked)
    m = _TOPIC_RE.match(text)
    if m and m.group("topic") in ATTENTION_TOPICS:
        text = ATTENTION_TOPICS[m.group("topic")] + text[m.end() - 2:]
    return text


def label(kind: str, width_: Optional[int] = None, stream=None) -> str:
    """The label that starts a `kind` message (note | warning | attention
    | error) printed to `stream` (stderr by default): `Info: ` /
    `Warning: ` (an ATTENTION one too) / `Error: ` shown to a person
    (width > 0), the captured `note: ` / `warning: ` / ... at width 0."""
    if kind not in _KINDS:
        raise ValueError(f"unknown message kind {kind!r}")
    w = width(sys.stderr if stream is None else stream) \
        if width_ is None else width_
    return (LABELS if w > 0 else _KINDS)[kind]


# A captured message line: optional indentation, an optional program
# name (`taxjson-gains`, `tjs run`, `tjs sum`), then a kind word in
# any of its old spellings (`note:`, `NOTE:`, `Note:`, `warning:`,
# `WARNING:`, `error:`, `ERROR:`) — or an already person-shaped label.
_CAPTURED_RE = re.compile(
    r"(?P<lead>[ \t]*)"
    r"(?:(?P<prog>(?:taxjson|tjs)(?:-[a-z0-9][\w-]*)?(?: [a-z][\w-]*)?): )?"
    r"(?P<kind>note|Note|NOTE|info|Info|INFO|warning|Warning|WARNING"
    r"|error|Error|ERROR): ")
_KIND_OF = {"note": "note", "info": "note", "warning": "warning",
            "error": "error"}


def relabel(line: str, *, source: bool = True) -> str:
    """A captured message line (`[<prog>: ]warning: ...`, an old `NOTE:`
    ...) as a person is shown it: the label first (`Warning: ...`,
    `Info: ...`), its indentation kept. `source`: a program name the
    line carried follows the label (`Error: taxjson-gains: ...`) — the
    line came from another program than the one the person ran (a
    stage's stderr the run echoes); False drops it (a program's own
    line). Any other line is returned as is. Display only: the captured
    text itself is never rewritten."""
    m = _CAPTURED_RE.match(line)
    if not m:
        return line
    kind = _KIND_OF[m.group("kind").lower()]
    head = LABELS[kind]
    prog = m.group("prog")
    rest = line[m.end():]
    if kind == "warning" and rest.startswith("ATTENTION: "):
        # The must-act channel is a Warning to a person (LABELS).
        rest = shown_topic(rest)
    return (m.group("lead") + head
            + (f"{prog}: " if prog and source else "")
            + rest)


def labelled(text: str, stream=None, *, source: bool = False) -> str:
    """`text` as printed to `stream` (stdout by default): each line
    relabel()ed when shown to a person (width > 0), as is when captured
    (width 0). For a message line built as text (`note: ...`) that is
    also written where a program reads it. Shown to a person, every
    control character but newline and tab is escaped (printable())."""
    if width(sys.stdout if stream is None else stream) <= 0:
        return str(text)
    # Shown to a person: a control character from the data (a broker
    # export's description, a plugin's message) is shown escaped, never
    # sent to the terminal (printable()).
    return "\n".join(relabel(printable(ln), source=source)
                     for ln in str(text).split("\n"))


def message(kind: str, text: str, *, prog: Optional[str] = None,
            details: Iterable[str] = (), width_: Optional[int] = None,
            stream=None) -> List[str]:
    """A diagnostic as lines: the label and a headline, then each detail
    as its own paragraph. `kind`: note | warning | attention (the run's
    ATTENTION channel) | error.

    Shown to a person (width > 0) the line starts with the label —
    `Info: ` / `Warning: ` (ATTENTION too, its topic capitalised) /
    `Error: ` — every later line (the headline's wrap, a detail, a `- `
    item and its wrapped lines) starts at column 0, and `prog` is not
    shown (it is the command the person ran). Printed with show(), a
    message of more than one line is followed by one blank line. A
    relayed captured line in a detail (`<prog>: error: ...`) is shown
    with its label first, as a message of its own (a blank line before
    it when the message so far spans more than one line). Captured for
    a program (width 0) it is `[<prog>: ]<kind>: <headline>`, lower
    case, the details indented two spaces — the bytes the run and the
    checklist read (label()).

    Keep the headline short and complete — what happened, to what — and
    put the why and the fix in `details`: a reader (and a grep for the
    marker) sees the headline line on its own."""
    if kind not in _KINDS:
        raise ValueError(f"unknown message kind {kind!r}")
    stream = sys.stderr if stream is None else stream
    w = width(stream) if width_ is None else width_
    head = LABELS[kind] if w > 0 else \
        (f"{prog}: " if prog else "") + _KINDS[kind]
    text = str(text).strip()
    if w <= 0:
        # Captured: the bytes programs read, as they always were (the
        # details indented two spaces, a `- ` item hanging under its
        # text).
        out = wrap(head + text, w, "", DETAIL_INDENT, stream)
        for d in details:
            if d is None:
                continue
            d = str(d).strip()
            hang = DETAIL_INDENT + ("  " if d.startswith("- ") else "")
            out.extend(wrap(d, w, DETAIL_INDENT, hang, stream))
        return out
    if kind == "attention":
        text = shown_topic(text)
    # Shown to a person: every line after the label's is flush-left.
    # The label never stands alone: a first word longer than the rest
    # of the line (a long path) stays on the label's line — it left a
    # bare "Error:" above the headline (backlog TJ-0123).
    out = []
    for part in (head.rstrip() + _NBSP + text).split("\n"):
        if part.strip():
            out.extend(wrap(part.strip(), w, "", "", stream))
    for d in details:
        if d is None:
            continue
        for x in str(d).strip().split("\n"):
            x = relabel(x).strip()
            if not x:
                continue
            if _SHOWN_LABEL_RE.match(x) and _entry_len(out) > 1:
                out.append("")
            out.extend(wrap(x, w, "", "", stream))
    return out


# A line a person is shown that starts a message.
_SHOWN_LABEL_RE = re.compile(r"(?:Info|Warning|Error): ")


def _entry_len(lines: Sequence[str]) -> int:
    """How many lines the last entry of `lines` spans (after its last
    blank line)."""
    n = 0
    for ln in reversed(lines):
        if not ln.strip():
            break
        n += 1
    return n


# ------------------------------------------------- one blank line after
# A message (or a wrapped step) that spans more than one line is
# followed by exactly one blank line on the console (docs/output-style.md,
# The run's console). The blank line is owed, not printed: it is printed
# before the next text written to the same destination (show(), or any
# write through a settling_streams() stream), so the output never ends
# with a blank line, and a stream redirected on its own (`2>err.txt`)
# keeps its own messages apart without a blank line from the other one.
# stdout and stderr share a destination when they are the same terminal,
# pipe or file (`2>&1`): {destination: weakref to the stream}.
_OWED: dict = {}


def real_stream(stream):
    """The stream a settling_streams() proxy writes to (else `stream`),
    through lib/cli_diag's guard beneath it (`_RoomGuard`, which names
    its stream the same way)."""
    s = getattr(stream, "_tj_inner", stream)
    while "_tj_inner" in getattr(s, "__dict__", ()):
        s = s.__dict__["_tj_inner"]
    return s


def _dest(stream):
    s = real_stream(stream)
    try:
        st = os.fstat(s.fileno())
        return ("fd", st.st_dev, st.st_ino)
    except Exception:                               # noqa: BLE001
        # No file descriptor (a StringIO: io.UnsupportedOperation), a
        # closed stream: the object itself.
        return ("obj", id(s))


def _owe(stream) -> None:
    import weakref
    s = real_stream(stream)
    try:
        ref = weakref.ref(s)
    except TypeError:
        ref = (lambda s=s: s)
    _OWED[_dest(s)] = ref


def _unowe(stream) -> None:
    if _OWED:
        _OWED.pop(_dest(stream), None)


def _take_owed(stream) -> bool:
    """True (and the debt cleared) when `stream`'s destination owes a
    blank line."""
    if not _OWED:
        return False
    key = _dest(stream)
    ref = _OWED.pop(key, None)
    if ref is None:
        return False
    if key[0] == "obj" and ref() is not real_stream(stream):
        return False        # another object that reused the id
    return True


def settle(stream=None) -> None:
    """Print the blank line `stream`'s destination owes (stdout by
    default), if any — before text a program prints there by other
    means (an exit message the interpreter prints)."""
    stream = sys.stdout if stream is None else stream
    if _take_owed(stream):
        try:
            real_stream(stream).write("\n")
        except (ValueError, OSError):
            pass


def _emit(chunks: Iterable[Tuple[Any, str, bool]], file) -> None:
    """The one print() of this module: each (text, end, flush) printed as
    print(text, end=end, flush=flush) prints it, to `file`."""
    for text, end, flush in chunks:
        print(text, end=end, file=file, flush=flush)


def _print_lines(lines: Iterable[Any], file) -> None:
    """Each line printed as print(line) prints it."""
    _emit(((ln, "\n", False) for ln in lines), file)


def put(lines, file=None) -> None:
    """Lines printed as print() prints each one, to `file` (stdout by
    default); a str is one print. Nothing is settled, wrapped, escaped or
    owed: the plain line writer of an action log or a message whose bytes
    are kept (show() owes a blank line after an entry of more than one
    line, which a raw print never did)."""
    file = sys.stdout if file is None else file
    _print_lines([lines] if isinstance(lines, str) else lines, file)


def join_blocks(blocks: Iterable[Sequence[str]]) -> List[str]:
    """Entries (each a list of lines: a message, a step) as one list of
    lines, one blank line after every entry of more than one line —
    none after the last."""
    out: List[str] = []
    last = 0
    for b in blocks:
        b = list(b)
        if not b:
            continue
        if last > 1:
            out.append("")
        out.extend(b)
        last = _entry_len(b)
    return out


def show(lines: Sequence[str], file=None, *, cont: bool = False) -> None:
    """Print one entry a person reads (a message, a step: its lines) to
    `file` (stderr by default): first the blank line the destination
    owes (settle), then the lines; an entry of more than one line owes
    one blank line before the next text there. `cont`: the lines
    continue the entry printed last (a captured continuation line the
    run echoes): no blank line between, and the entry now spans more
    than one line. Captured for a program (width 0) the lines are
    printed as they are, owing nothing."""
    file = sys.stderr if file is None else file
    lines = list(lines)
    if not lines:
        return
    if width(file) <= 0:
        _print_lines(lines, file)
        return
    # One item may hold several lines (exit_text, labelled join theirs).
    lines = [x for ln in lines for x in str(ln).split("\n")]
    if cont or not lines[0].strip():
        # A continuation, or an entry with its own blank line first.
        _unowe(file)
    else:
        settle(file)
    _print_lines(lines, file)
    if cont or _entry_len(lines) > 1:
        _owe(file)


def show_listed(text: str, file=None, indent: str = "  ",
                hang: str = "    ") -> None:
    """One line of a listing, to `file` (stdout by default): wrapped under
    `indent`, or, labelled and shown to a person, a message of its own,
    flush-left. Captured (width 0) it keeps its indent."""
    file = sys.stdout if file is None else file
    shown = labelled(text, file)
    if shown != text and width(file) > 0:
        indent = hang = ""
    show(wrap(shown, None, indent, hang, stream=file), file)


def show_blocks(blocks: Iterable[Sequence[str]], file=None) -> None:
    """show() each entry in turn."""
    for b in blocks:
        show(b, file)


class _Settling:
    """A text stream that writes the blank line its destination owes
    (show()) before the next text written to it. A write that starts with
    a newline (a program's own blank line, `print("\\nNext:")`) pays the
    debt instead: never two blank lines in a row."""

    def __init__(self, inner):
        self._tj_inner = inner

    def write(self, s):
        if _OWED and s and _take_owed(self._tj_inner):
            if not s.startswith("\n"):
                self._tj_inner.write("\n")
        return self._tj_inner.write(s)

    def __getattr__(self, name):
        return getattr(self._tj_inner, name)


@contextlib.contextmanager
def settling_streams():
    """Within it, sys.stdout and sys.stderr write the blank line a
    multi-line message owes (show()) before whatever any code prints
    next to the same destination — a report's raw print() included.
    The top of a process only (lib/cli_diag.run_top_level). A stream
    the process started without (`>&-`, `2>&-`: None) stays None —
    print() to it is a no-op, as it was before the proxies; wrapped,
    every write raised AttributeError. (run_top_level makes such a
    stream /dev/null before it: lib/cli_diag `_closed_streams_to_devnull`.)"""
    saved = sys.stdout, sys.stderr
    if sys.stdout is not None and not isinstance(sys.stdout, _Settling):
        sys.stdout = _Settling(sys.stdout)
    if sys.stderr is not None and not isinstance(sys.stderr, _Settling):
        sys.stderr = _Settling(sys.stderr)
    try:
        yield
    finally:
        # (a caller that replaced them meanwhile — redirect_stdout —
        # restored its own; put back the ones found at entry)
        sys.stdout, sys.stderr = saved


def emit(kind: str, text: str, *, prog: Optional[str] = None,
         details: Iterable[str] = (), file=None, whole: bool = False) -> None:
    """Print message(...) to `file` (stderr by default), show()n: one
    blank line after it when it spans more than one line. With the
    one-line switch on (concise_on: a default view, Essentials first)
    a note or warning is its headline and the command its detail
    names, one line (concise_show); an error keeps its fix lines, and
    so does `whole`: a message whose cut would lose what the person
    must know (a report is incomplete, a paper must be filed)."""
    file = sys.stderr if file is None else file
    if kind != "error" and not whole and concise_on(file):
        text = str(text).strip().split("\n")[0]
        if kind == "attention":
            text = shown_topic(text)
        details = [str(d) for d in details if d is not None]
        concise_show(concise_line(LABELS[kind] + text, details,
                                  width(file)), file)
        return
    show(message(kind, text, prog=prog, details=details, stream=file),
         file)


def note(text: str, *, prog: Optional[str] = None,
         details: Iterable[str] = (), file=None,
         whole: bool = False) -> None:
    emit("note", text, prog=prog, details=details, file=file, whole=whole)


def warn(text: str, *, prog: Optional[str] = None,
         details: Iterable[str] = (), file=None,
         whole: bool = False) -> None:
    emit("warning", text, prog=prog, details=details, file=file, whole=whole)


def attention(text: str, *, prog: Optional[str] = None,
              details: Iterable[str] = (), file=None,
              whole: bool = False) -> None:
    emit("attention", text, prog=prog, details=details, file=file, whole=whole)


def error(text: str, *, prog: Optional[str] = None,
          details: Iterable[str] = (), file=None) -> None:
    emit("error", text, prog=prog, details=details, file=file)


# The program name in front of a refusal written the old way.
_PROG_RE = re.compile(
    r"(?:taxjson|tjs)(?:-[a-z0-9][\w-]*)?(?: [a-z][\w-]*)?"
    r"(?: --[a-z][\w-]*)?: ")


def exit_text(text: str, stream=None) -> str:
    """The text for `sys.exit(...)` of a refusal written the old way —
    `<prog>: <what>` or `<prog>: error: <what>`: as is when captured
    (width 0 — `tjs checklist` shows a failed command's last line,
    the run relays a stage's), else as message() lays out an error for
    the person: `Error: <what>`, the program name dropped, wrapped."""
    text = str(text)
    stream = sys.stderr if stream is None else stream
    w = width(stream)
    if w <= 0:
        return text
    m = _PROG_RE.match(text)
    rest = text[m.end():] if m else text
    k = _CAPTURED_RE.match(rest)
    if k and not k.group("lead") and not k.group("prog"):
        kind = _KIND_OF[k.group("kind").lower()]
        rest = rest[k.end():]
    else:
        kind = "error"
    return "\n".join(message(kind, rest, width_=w, stream=stream))


def fail(text: str, *, prog: Optional[str] = None,
         details: Iterable[str] = (), code: int = 1):
    """Stop with an error message (headline + details, as message()).
    Exit 1 (the command's refusal or finding) raises SystemExit(<the
    text>), as `sys.exit(msg)` does — the interpreter prints it to
    stderr, and an in-process caller still reads it from the exception;
    exit 2 (a usage or input error, lib/cli_diag) prints it first."""
    lines = message("error", text, prog=prog, details=details,
                    stream=sys.stderr)
    if code == 1:
        raise SystemExit("\n".join(lines))
    show(lines, sys.stderr)
    raise SystemExit(code)


# -------------------------------------------------------------- tables
from taxjson.lib.report_fmt import auto_aligns as _auto_aligns  # noqa: E402


def records(headers: Sequence[str], body: Sequence[Sequence[str]],
            width_: Optional[int] = None, indent: str = "",
            key=0) -> List[str]:
    """The per-record layout of a table too wide for the width: one block
    per row — its `key` column(s) (an index or a tuple of indexes) on the
    first line, then `label value` pairs packed onto indented lines (empty
    cells left out) — and a blank line between blocks."""
    w = width(None) if width_ is None else width_
    keys = (key,) if isinstance(key, int) else tuple(key)
    out: List[str] = []
    for r in body:
        if out:
            out.append("")
        cells = [str(c) for c in r]
        out.append(indent + "  ".join(cells[k] for k in keys
                                      if k < len(cells) and cells[k]))
        pairs = [f"{str(h).replace(chr(10), ' ').lower()} {c}"
                 for i, (h, c) in enumerate(zip(headers, cells))
                 if i not in keys and c not in ("", None)]
        out.extend(wrap("   ".join(p.replace(" ", _NBSP) for p in pairs),
                        w, indent + DETAIL_INDENT, indent + DETAIL_INDENT))
    return [ln.replace(_NBSP, " ") for ln in out]


def fit_table(headers: Sequence[str], body: Sequence[Sequence[str]], *,
              aligns: Optional[Sequence[str]] = None,
              foot: Sequence[Sequence[str]] = (),
              drop: Sequence[int] = (), width_: Optional[int] = None,
              indent: str = "", gap: str = "  ",
              per_record: bool = True, key=0) -> List[str]:
    """A table (report_fmt.render_table: header, `---` rule, rows, an
    optional ruled-off foot) that fits the width. Numeric columns
    right-align unless `aligns` says otherwise. Too wide: the columns in
    `drop` (indexes, least important first) are dropped one at a time;
    still too wide, the rows switch to the per-record layout
    (`per_record=False` keeps the over-wide table instead; `key` names
    the column(s) that head each record, as records()). Rows are
    never wrapped mid-row. Width 0: the full table."""
    w = width(None) if width_ is None else width_
    cell = printable if w > 0 else str      # shown to a person: escaped
    body = [[("" if c is None else cell(c)) for c in r] for r in body]
    foot = [[("" if c is None else cell(c)) for c in r] for r in foot]
    headers = [cell(h) for h in headers]
    aligns = list(aligns) if aligns else _auto_aligns(headers, body)
    keep = list(range(len(headers)))

    def _render(cols):
        return [indent + ln.rstrip() for ln in render_table(
            [headers[i] for i in cols], [aligns[i] for i in cols],
            [[r[i] if i < len(r) else "" for i in cols] for r in body],
            [[r[i] if i < len(r) else "" for i in cols] for r in foot],
            gap=gap)]

    lines = _render(keep)
    if w <= 0 or max(map(len, lines), default=0) <= w:
        return lines
    for i in drop:
        if i in keep and len(keep) > 1:
            keep.remove(i)
            lines = _render(keep)
            if max(map(len, lines)) <= w:
                return lines
    if not per_record:
        return lines
    keys = (key,) if isinstance(key, int) else tuple(key)
    keep = list(range(len(headers)))   # a record shows every column
    return records(headers, body + foot, w, indent,
                   tuple(k for k in keys if k in keep))


class Verbatim(str):
    """A value printed as is, never wrapped: a command to copy, a path."""


def kv_lines(pairs: Iterable[Tuple[str, str]], indent: str = "",
             width_: Optional[int] = None) -> List[str]:
    """`label:  value` lines with the values aligned; a long value wraps
    under itself (a Verbatim value never wraps)."""
    pairs = [(str(k), "" if v is None else v) for k, v in pairs]
    if not pairs:
        return []
    w = width(None) if width_ is None else width_
    if w > 0:
        pairs = [(printable(k), Verbatim(printable(v))
                  if isinstance(v, Verbatim) else v) for k, v in pairs]
    lw = max(len(k) for k, _ in pairs) + 1
    out: List[str] = []
    for k, v in pairs:
        lead = f"{indent}{k + ':':<{lw}}  "
        if isinstance(v, Verbatim):
            out.append(lead + v)
        else:
            out.extend(wrap(str(v), width_, lead, " " * len(lead)))
    return out


def relpath(path, root=None) -> str:
    """`path` relative to `root` (the project) when inside it, else as
    given — `inputs/margin/manifest.json`, not the absolute path."""
    from pathlib import Path
    p = Path(path)
    if root is not None:
        try:
            return str(p.resolve().relative_to(Path(root).resolve()))
        except (ValueError, OSError):
            pass
    return str(p)


# ------------------------------------------------------------- builder
# What `--json` prints by default: pipeline/sanity._json_out's json.dumps
# keywords, and `clean` (no json.dumps keyword): every non-finite float
# as null (clean_json). A site's own spec says `clean` too, True or False
# (render() refuses any other): the wash radar's keywords leave NaN as
# json.dumps writes it, and a forgotten key must not change that.
JSON_SPEC: Dict[str, Any] = {"indent": 2, "sort_keys": True, "default": str, "clean": True}


def clean_json(v):
    """`v` with every non-finite float (NaN, an infinity) as None, in
    dicts, lists and tuples at any depth (a tuple becomes a list, as
    json.dumps writes it). STRICT JSON: json.dumps' default allow_nan
    wrote bare NaN / Infinity tokens, which are invalid JSON (node's
    JSON.parse throws, jq corrupts them: REVIEW #41)."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, dict):
        return {k: clean_json(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean_json(x) for x in v]
    return v


class Doc:
    """A report built section by section, printed in the house layout:
    a title line, sections separated by exactly one blank line, each
    with a short heading; prose wrapped to the width; `- ` items with a
    hanging indent; aligned key/value blocks; fitted tables.

        d = Doc("WASH SALES — USD, tax year 2025")
        d.table(headers, rows, drop=(5,))
        d.section("Manual check")
        d.item("ABC loss on 2025-03-01: ...")
        d.print()

    Recorded: each call adds an op, its lines made by that call at the
    Doc's width (fixed when the Doc is made: a builder that reuses a list
    it passed, or passes a generator, changes nothing already added);
    lines() and print() put the ops together. The parts of a report have
    their own calls: legend() (one or two lines above a table), act()
    (an `! ` line after the data), hint() (the closing `More:` line).
    Ops added inside `with d.details():` belong to the long form only:
    lines(details=True) and print(details=True) show them (`--details`
    as data, not a global). `json` (with `json_spec`, JSON_SPEC by
    default; a site's own says `clean`) is what `--json` prints of the
    same report (render()).

    `exact=True` (PLAN step 15: a report whose raw print() calls become a
    Doc keeps their bytes): each op's lines as its call made them — no
    trailing space stripped, no blank line folded or trimmed, the title
    (an empty one too: a blank line) and line() and section() text
    printed as print() prints it (no control character escaped, nothing
    wrapped), no blank line added after a message, and nothing settled
    before it (a raw print never settled: the top of a process's
    settling_streams() does, as it did); raw(*objects, sep=, end=,
    flush=) is one print() call. para(),
    item(), kv(), table() and message() make the same lines in either
    mode (wrap(), kv_lines(), fit_table(), message()).

    `live=True` (an exact Doc only): each op is also printed the moment
    it is added, as the print() calls it replaces printed it then: to
    `stream`, or to sys.stdout as it is at that moment. A report that
    stops part-way (an error, a reader gone from a pipe) has printed what
    the raw prints had, and nothing it had not. It keeps no line, so a
    long report streams as its raw prints did (TJ-4873: `tjs audit`'s
    blocks, TJ-0334): lines(), text(), print() and details() refuse; the
    long form is the builder's `if details:`, as it was.
    """

    def __init__(self, title: Optional[str] = None, *,
                 width_: Optional[int] = None, stream=None,
                 exact: bool = False, live: bool = False):
        if live and not exact:
            raise ValueError("Doc: live is for an exact Doc only (exact=True)")
        self.stream = stream
        self.w = width(stream) if width_ is None else width_
        self.exact = exact
        self.live = live
        self.json: Any = None
        self.json_spec: Dict[str, Any] = dict(JSON_SPEC)
        # (kind, [(text, end, flush)], in the long form only)
        self._ops: List[Tuple[str, List[Tuple[Any, str, bool]], bool]] = []
        self._in_details = False
        if exact:
            # print("") printed a blank line: so does an empty title.
            if title is not None:
                self._raw("title", str(title))
        elif title:
            self._add("title", wrap(title, self.w))

    def _push(self, kind: str, chunks: List[Tuple[Any, str, bool]]) -> "Doc":
        if self.live:
            # Printed now and not kept: a live Doc's report streams.
            _emit(chunks, sys.stdout if self.stream is None else self.stream)
            return self
        self._ops.append((kind, chunks, self._in_details))
        return self

    def _add(self, kind: str, lines: Iterable[Any]) -> "Doc":
        return self._push(kind, [(ln, "\n", False) for ln in lines])

    def _raw(self, kind: str, text: str, end: str = "\n",
             flush: bool = False) -> "Doc":
        return self._push(kind, [(text, end, flush)])

    def _verbatim(self, kind: str, text) -> "Doc":
        if self.exact:
            return self._raw(kind, str(text))
        text = printable(text) if self.w > 0 else str(text)
        return self._add(kind, text.split("\n"))

    @contextlib.contextmanager
    def details(self):
        """Within it, every op added is part of the long form only
        (shown by lines(details=True), print(details=True)). A live Doc
        refuses: it has printed each op already."""
        if self.live:
            raise ValueError("Doc.details: a live Doc prints each op as it is added")
        was, self._in_details = self._in_details, True
        try:
            yield self
        finally:
            self._in_details = was

    # Each call appends lines; a section() opens with one blank line.
    def blank(self) -> "Doc":
        return self._add("blank", [""])

    def line(self, text: str = "") -> "Doc":
        """A verbatim line (a command to copy, a pre-formatted row);
        control characters escaped when shown to a person (exact: as
        print(text) prints it)."""
        return self._verbatim("line", text)

    def rows(self, lines: Iterable[str]) -> "Doc":
        """Lines another function laid out (a report table's), each as
        line() adds it."""
        for ln in list(lines):
            self._verbatim("rows", ln)
        return self

    def raw(self, *objects, sep: Optional[str] = " ",
            end: Optional[str] = "\n", flush: bool = False) -> "Doc":
        """One print(*objects, sep=sep, end=end, flush=flush), its bytes
        as print() writes them: an exact Doc only."""
        if not self.exact:
            raise ValueError("Doc.raw: an exact Doc only (exact=True)")
        return self._raw("raw", (" " if sep is None else sep).join(map(str, objects)),
                         "\n" if end is None else end, flush)

    def section(self, heading: str) -> "Doc":
        self.blank()
        if self.exact:
            return self._raw("heading", str(heading))
        return self._add("heading", [printable(heading) if self.w > 0 else heading])

    def para(self, text: str, indent: str = "",
             hang: Optional[str] = None) -> "Doc":
        """Prose wrapped to the width, the first line at `indent`, the
        rest at `hang` (default: `indent`)."""
        return self._add("para", wrap(text, self.w, indent,
                                      indent if hang is None else hang))

    def item(self, text: str, indent: str = "", bullet: str = "- ") -> "Doc":
        return self._add("item", wrap(text, self.w, indent + bullet,
                                      indent + " " * len(bullet)))

    def items(self, texts: Iterable[str], indent: str = "") -> "Doc":
        for t in list(texts):
            self.item(t, indent)
        return self

    def kv(self, pairs: Iterable[Tuple[str, str]],
           indent: str = "") -> "Doc":
        return self._add("kv", kv_lines(pairs, indent, self.w))

    def table(self, headers, body, **kw) -> "Doc":
        kw.setdefault("width_", self.w)
        return self._add("table", fit_table(headers, body, **kw))

    def legend(self, text: str) -> "Doc":
        """The one or two lines that explain the table below them
        (docs/output-style.md, Essentials first): verbatim, as line()."""
        return self._verbatim("legend", text)

    def act(self, text: str, cmd: Optional[str] = None) -> "Doc":
        """An act-on line after the data: act(text, cmd)."""
        return self._add("act", [act(text, cmd)])

    def hint(self, cmd: str, what: str = "notes") -> "Doc":
        """The closing pointer to the long form: details_hint(cmd, what)."""
        return self._add("hint", [details_hint(cmd, what)])

    def message(self, kind: str, text: str, details=(),
                prog: Optional[str] = None) -> "Doc":
        lines = message(kind, text, prog=prog, details=details,
                        width_=self.w)
        self._add("message", lines)
        if self.w > 0 and _entry_len(lines) > 1 and not self.exact:
            # Shown: one blank line after a message of more than one
            # line (lines() drops it at the end, never doubles it).
            self._add("blank", [""])
        return self

    def _chunks(self, details: bool = False) -> List[Tuple[Any, str, bool]]:
        return [c for _kind, chunks, long_form in self._ops
                if details or not long_form for c in chunks]

    def lines(self, details: bool = False) -> List[str]:
        """The lines (`details`: the long form's too): no leading or
        trailing blank line, never two blank lines in a row, no trailing
        spaces. Exact: the lines as printed, as they are. A live Doc
        refuses: it printed each op when it was added and kept none."""
        if self.live:
            raise ValueError("Doc.lines: a live Doc printed its lines and kept none")
        if self.exact:
            printed = "".join(str(t) + e for t, e, _f in self._chunks(details))
            parts = printed.split("\n")
            if parts[-1] == "":
                parts.pop()
            return parts
        out: List[str] = []
        for ln, _end, _flush in self._chunks(details):
            ln = ln.rstrip()
            if not ln and (not out or not out[-1]):
                continue
            out.append(ln)
        while out and not out[-1]:
            out.pop()
        return out

    def text(self, details: bool = False) -> str:
        return "\n".join(self.lines(details))

    def print(self, file=None, details: bool = False) -> None:
        """The text to `file` (stdout by default): the blank line the
        destination owes first when shown to a person (settle), then the
        lines. Exact: each op's print() as it was, nothing settled. A
        live Doc refuses: it printed each op when it was added."""
        if self.live:
            raise ValueError("Doc.print: a live Doc has printed its lines already")
        file = sys.stdout if file is None else file
        if self.exact:
            _emit(self._chunks(details), file)
            return
        if self.w > 0:
            settle(file)
        _print_lines(self.lines(details), file)


def render(doc: Doc, *, as_json: bool = False, details: bool = False,
           file=None) -> None:
    """Print a report to `file` (stdout by default): `as_json`, its
    `json` as `--json` prints it (json.dumps with `json_spec`'s keywords,
    non-finite floats null when its `clean` is true, and a newline); else
    its text (Doc.print; `details`: the long form). A `json_spec` whose
    `clean` is not True or False (left out, None, 1) is refused
    (ValueError), as no default would be safe: True turns a site's NaN
    into null, False a `_json_out` site's null into NaN."""
    file = sys.stdout if file is None else file
    if as_json:
        spec = dict(doc.json_spec)
        if not isinstance(spec.get("clean"), bool):
            raise ValueError("Doc.json_spec: `clean` must be True "
                             "(non-finite floats as null) or False (as "
                             "json.dumps writes them)")
        payload = clean_json(doc.json) if spec.pop("clean") else doc.json
        _print_lines([json.dumps(payload, **spec)], file)
        return
    doc.print(file, details=details)


# ---------------------------------------------------------------- lint
# Retired at the start of a line shown to a person: the captured,
# lower-case labels (`note:`, `warning:`, `error:`) and their old
# spellings, with or without a program name in front, and the old
# bullets; a person label (`Info:` / `Warning:` / `Error:`) behind a
# program name (`taxjson-x: Warning:`) — the label starts the line.
_RETIRED_PREFIXES = re.compile(
    r"^\s*(?:(?:(?:taxjson|tjs)[\w -]*:\s+|[\w./-]+:\s+)?(?:note:|NOTE:|Note:|warning:|WARNING:"
    r"|error:|ERROR:|!!|→ |-> |\*\*\* )"
    r"|(?:taxjson|tjs)[\w -]*:\s+(?:Info|Warning|Error):)")


def _looks_like_table_row(line: str) -> bool:
    t = line.strip()
    if re.fullmatch(r"[-=─_+|\s]{3,}", t):
        return True
    return len(re.findall(r"\S {2,}(?=\S)", t)) >= 2


def lint(text: str, width_: int = WIDTH,
         allow: Iterable[str] = ()) -> List[str]:
    """Style problems in rendered output, for tests: a prose line longer
    than `width_` (a table row, or a line containing one of the `allow`
    substrings — a command to copy — is exempt), two blank lines in a
    row, a leading or trailing blank line, a retired prefix (a
    lower-case `note:` / `warning:` / `error:` or NOTE: / WARNING: at
    the start of a line, a label behind a program name, !!, →, ***).
    [] when clean."""
    allow = tuple(allow)
    probs: List[str] = []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if lines and not lines[0].strip():
        probs.append("leading blank line")
    if lines and not lines[-1].strip():
        probs.append("trailing blank line")
    for i, ln in enumerate(lines, 1):
        # A line that is one unbreakable word (a long path or URL) cannot
        # wrap; it is not a style problem.
        if (len(ln) > width_ and not _looks_like_table_row(ln)
                and len(ln.split()) > 1
                and not any(a in ln for a in allow)):
            probs.append(f"line {i}: {len(ln)} columns: {ln[:60]}...")
        if i > 1 and not ln.strip() and not lines[i - 2].strip():
            probs.append(f"line {i}: two blank lines in a row")
        if _RETIRED_PREFIXES.match(ln):
            probs.append(f"line {i}: retired prefix: {ln[:40]}")
    return probs


# A line of a console a person reads (`tjs run`, a command's
# messages) that starts an entry: a step (`==> `) or a message label.
# Any other non-blank line continues the entry above it, flush-left.
CONSOLE_LINE_RE = re.compile(r"(?:==> |Info: |Warning: |Error: )")


def console_lint(text: str, width_: int = WIDTH,
                 allow: Iterable[str] = ()) -> List[str]:
    """lint() plus the console rule (docs/output-style.md, The run's
    console): every non-blank line starts with `==> `, `Info: `,
    `Warning: ` or `Error: `, or continues the entry (step or message)
    on the line directly above, flush-left; a blank line comes only
    after an entry of more than one line, and such an entry is always
    followed by one (unless it ends the text); no `ATTENTION:` word and
    no `(content: ...)` detection detail. [] when clean."""
    probs = lint(text, width_, allow)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    entry = 0                   # lines of the entry so far (0: none)
    for i, ln in enumerate(lines, 1):
        if not ln.strip():
            if entry < 2:
                probs.append(f"line {i}: blank line not after a message "
                             f"of more than one line")
            entry = 0
            continue
        if CONSOLE_LINE_RE.match(ln):
            if entry > 1:
                probs.append(f"line {i}: no blank line after the "
                             f"{entry}-line message above")
            entry = 1
        else:
            if not entry:
                probs.append(f"line {i}: not a step, label or "
                             f"continuation: {ln[:60]!r}")
            elif ln[:1].isspace():
                probs.append(f"line {i}: indented continuation: "
                             f"{ln[:60]!r}")
            entry += 1
        if "ATTENTION:" in ln:
            probs.append(f"line {i}: ATTENTION word shown: {ln[:60]}")
        if "(content: " in ln:
            probs.append(f"line {i}: detection detail shown: {ln[:60]}")
    return probs


# ------------------------------------------------------- essentials first
# docs/output-style.md, "Essentials first": a legend of one or two short
# lines BEFORE the table it explains; after the data only what the reader
# must act on (`! ` lines) or must not miss, each one line naming the
# command with the detail; the explanations behind `--details`.
ACT = "! "


def act(text: str, cmd: Optional[str] = None) -> str:
    """One act-on line: `! <text> — <cmd>` (cmd: the command that gives
    the detail, `tjs ...`). Never wrapped: keep it short (the tests hold
    it to 100 columns)."""
    return ACT + printable(text) + (f" — {cmd}" if cmd else "")


def details_hint(cmd: str, what: str = "notes") -> str:
    """The closing pointer to the long form: `More: <cmd> (<what>)`."""
    return f"More: {cmd} ({what})" if what else f"More: {cmd}"


_UPPER_HEAD_RE = re.compile(r"^(?:\d+\.\s+)?[A-Z0-9][A-Z0-9 &/().,'+#-]*"
                            r"[A-Z0-9)](?:\s+[—-]\s+.*)?$")
_KV_FIGURE_RE = re.compile(r"^\s*[^:]{1,48}:\s+[-+(]?[$€£]?\(?[\d.,]+")
_KV_ALIGNED_RE = re.compile(r"^\s*[^:\s][^:]{0,46}:\s{2,}\S")
_LABEL_START_RE = re.compile(r"^(?:Info|Warning|Error): |^! ")


def classify(text: str) -> List[Tuple[str, str]]:
    """Each non-blank line of rendered output with its kind, for the
    concise-output budget (docs/output-style.md, Essentials first):
    "table" (a table's header, rule or row; a per-record block),
    "heading" (the title, an upper-case section heading), "step" (`==> `),
    "figure" (an aligned `label:  value` line, or `label: <number>`),
    "prose" (everything else: a legend, a note, a message, an `! ` line).
    The budget counts the prose lines."""
    lines = [ln.rstrip() for ln in text.split("\n")]
    gap = re.compile(r"\S {2,}(?=\S)")
    rule = re.compile(r"[-=─━═_+|\s]{3,}")
    # Blocks of consecutive non-blank lines: a block with a rule line or
    # two or more lines with column gaps is a table; its gapped lines
    # (and the line above a rule) are table lines.
    kinds: List[Optional[str]] = [None] * len(lines)
    i = 0
    while i < len(lines):
        if not lines[i].strip():
            i += 1
            continue
        j = i
        while j < len(lines) and lines[j].strip():
            j += 1
        blk = range(i, j)
        rules = [k for k in blk if rule.fullmatch(lines[k].strip())]
        gapped = [k for k in blk if gap.search(lines[k].strip())
                  and not _LABEL_START_RE.match(lines[k].lstrip())]
        if rules or len(gapped) >= 2:
            for k in rules:
                kinds[k] = "table"
                if k - 1 >= i:
                    kinds[k - 1] = "table"
            for k in gapped:
                kinds[k] = "table"
        i = j
    out: List[Tuple[str, str]] = []
    first = True
    for k, ln in enumerate(lines):
        t = ln.strip()
        if not t:
            continue
        kind = kinds[k]
        if kind is None:
            if t.startswith("==> "):
                kind = "step"
            elif _LABEL_START_RE.match(ln.lstrip()):
                kind = "prose"
            elif (len(t) <= 110 and _UPPER_HEAD_RE.match(t)
                    and sum(c.isalpha() for c in t.split(" — ")[0]) >= 3):
                kind = "heading"
            elif first and " — " in t and t.split(" — ")[0].isupper():
                kind = "heading"
            elif ((_KV_ALIGNED_RE.match(ln) or _KV_FIGURE_RE.match(ln))
                    and not t.startswith(("- ", "* "))):
                kind = "figure"
            elif _LOOKS_TABLE(t):
                kind = "table"
            else:
                kind = "prose"
        first = False
        out.append((kind, ln))
    return out


def _LOOKS_TABLE(t: str) -> bool:
    return _looks_like_table_row(t)


def prose_lines(text: str) -> List[str]:
    """The lines the concise budget counts (classify(): "prose")."""
    return [ln for kind, ln in classify(text) if kind == "prose"]


def act_lines(text: str) -> List[str]:
    """The act-on lines (`! ...`)."""
    return [ln for ln in text.split("\n") if ln.startswith(ACT)]


def act_list(text: str, names: Sequence[str], cmd: Optional[str] = None,
             width_: int = 100) -> str:
    """An act-on line naming items: `! <text> (A, B +3 more) — <cmd>`,
    as many names as fit in `width_` columns; none when not even one
    fits (a 21-character option symbol: the command lists them; backlog
    TJ-0229), unless the line is too long without them too."""
    names = [printable(n) for n in names]
    for k in range(len(names), 0, -1):
        more = len(names) - k
        inner = ", ".join(names[:k]) + (f" +{more} more" if more else "")
        line = act(f"{text} ({inner})", cmd)
        if len(line) <= width_:
            return line
    bare = act(text, cmd)
    if len(bare) <= width_ or not names:
        return bare
    return act(f"{text} ({names[0]}"
               + (f" +{len(names) - 1} more" if len(names) > 1 else "")
               + ")", cmd)


# ---------------------------------------------- one-line messages, folded
# Set while a command's messages are shown one line each (tjs run's
# console, and every --details command's default view: cli/main
# _brief_messages): lib/stage_msg's say / emit_line and emit() here.
CONCISE_ENV = "TAXJSON_RUN_CONCISE"
# A message of one kind is shown this many times; the rest are counted
# and said in one line at the end (fold_summary).
FOLD_SHOWN = 2
_FOLD: dict = {}


def concise_on(file=None) -> bool:
    """One-line messages: the switch is on and `file` is the process's
    own stream shown to a person (width > 0; a redirected buffer or a
    capture keeps every byte)."""
    file = sys.stderr if file is None else file
    return (os.environ.get(CONCISE_ENV) == "1" and width(file) > 0
            and real_stream(file) in (sys.__stderr__, sys.__stdout__))


def _fold_key(line: str) -> str:
    """A message's kind: its text with the symbols, numbers, quoted code
    and paths taken out (`Warning: Income year: X: distribution 9 CAD
    paid 9 ...`), its first 60 characters."""
    t = re.sub(r"`[^`]*`", "`X`", line)
    t = re.sub(r"\S*[/\\]\S*", "P", t)
    t = re.sub(r"\b[A-Z][A-Z0-9]*(?:[.:_-][A-Z0-9]+)*\b(?![a-z])", "X", t)
    t = re.sub(r"\d[\d,.]*", "9", t)
    return t[:60]


def concise_show(line: str, file=None) -> None:
    """Show one message as one line: cut at a word to the width (` ...`
    marks the cut; --details has it whole), and the third and later
    message of one kind (_fold_key) counted, not shown."""
    file = sys.stderr if file is None else file
    line = printable(" ".join(str(line).split()))
    key = _fold_key(line)
    n, first = _FOLD.get(key, (0, line))
    _FOLD[key] = (n + 1, first)
    if n >= FOLD_SHOWN:
        return
    show([truncate(line, width(file))], file)


def truncate(line: str, width_: int) -> str:
    """`line` cut at a word to `width_` columns, ` ...` marking the cut
    (width 0: whole)."""
    if 0 < width_ < len(line):
        cut = line[:width_ - 4]
        cut = cut[:cut.rfind(" ")] if " " in cut else cut
        return cut.rstrip(" ,;:—-") + " ..."
    return line


def fold_summary(file=None) -> None:
    """One line per message kind shown FOLD_SHOWN times and said again:
    `Info: 7 more like "Warning: Income year: ..." (--details shows each)`.
    Then forget them."""
    file = sys.stderr if file is None else file
    items = [(n, first) for n, first in _FOLD.values() if n > FOLD_SHOWN]
    _FOLD.clear()
    w = width(file) or WIDTH
    for n, first in items:
        tail = "\" (--details shows each)"
        head = f"Info: {n - FOLD_SHOWN} more like \""
        room = max(20, w - len(head) - len(tail))
        quote = first if len(first) <= room else first[:room - 3] + "..."
        show([head + quote + tail], file)


# Where a long captured one-liner splits into headline + detail: the
# earliest clause break (an em-dash, a sentence end, a semicolon, or the
# colon after a parenthesised subject) past the first _MIN_HEAD columns.
_BREAKS = (" — ", ". ", "; ", "): ")
# A headline shorter than this is not worth a split (the marker and the
# topic alone).
_MIN_HEAD = 40


def split_message(text: str):
    """(headline, rest) of a captured one-line message, or (text, '')."""
    best = None
    # A break inside a `code span` is no break: one past an odd number of
    # backticks. Their positions, once (linear in the text, however many
    # breaks an unclosed span holds).
    ticks = [j for j, c in enumerate(text) if c == "`"]
    # Nor is one inside parentheses (`(a trust's, s.104(13); the T3)`):
    # the paren depth before each position, once.
    depth, d = [], 0
    for c in text:
        depth.append(d)
        d += (c == "(") - (c == ")")
    depth.append(d)

    def _inside(i: int) -> bool:
        # (the `): ` break closes its group: judged after the paren)
        j = i + 1 if text.startswith("): ", i) else i
        return bool(bisect_left(ticks, i) % 2) or depth[j] > 0

    for brk in _BREAKS:
        i = text.find(brk, _MIN_HEAD)
        while i >= 0 and _inside(i):
            i = text.find(brk, i + 1)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, brk)
    if best is None:
        return text, ""
    i, brk = best
    keep = 1 if brk in (". ", "): ") else 0
    head = text[:i + keep]
    rest = text[i + len(brk):].strip()
    if not rest:
        return text, ""
    # The detail is a sentence of its own: capitalised — after a `; `
    # only when it starts with a plain word (never an id or a `code`).
    if rest[0].islower() and (brk != "; " or re.match(r"[a-z]+ ", rest)):
        rest = rest[0].upper() + rest[1:]
    return head, rest


# Essentials first (docs/output-style.md): the run's console shows each
# message as ONE line, its headline naming the command with the detail;
# `tjs run --details` (and the captured .diag) keep the rest.
_CMD_RE = re.compile(r"`((?:tjs|taxjson) [^`]+)`")


def detail_command(texts: Iterable[str]) -> Optional[str]:
    """The first `tjs ...` command a message's detail names (the
    command that lists or fixes it), or None."""
    for t in texts:
        m = _CMD_RE.search(t or "")
        if m:
            return m.group(1)
    return None


def concise_line(text: str, details: Iterable[str] = (),
                 width_: int = 0, cmd: Optional[str] = None) -> str:
    """A message as one line: its headline (split_message) and, when the
    headline names no command, ` — `<the command its detail names>``
    (just `tjs <command>`, then `tjs <command>`, when the whole one
    would not fit `width_`; the headline alone when none fits). `cmd`:
    the command to name instead. `text` carries its label."""
    if width_ > 0 and len(text) <= width_:
        # It fits: the whole text is its one line.
        head, rest = text, ""
    else:
        head, rest = split_message(text)
    if "`taxjson " in head or "`tjs " in head:
        return head
    cmd = cmd or detail_command(([rest] if rest else []) + list(details))
    if not cmd:
        return head
    full = f"{head} — `{cmd}`"
    if width_ <= 0 or len(full) <= width_:
        return full
    for cand in (f"{head} — `{' '.join(cmd.split()[:2])}`",
                 f"{head} — `tjs {' '.join(cmd.split()[1:2])}`"):
        if len(cand) <= width_:
            return cand
    # The headline alone fits: one line (the command is in --details).
    return head if len(head) <= width_ else cand
