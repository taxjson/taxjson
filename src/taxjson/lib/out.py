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
  * lib/report_model — `render_table` (structured cells), `fmt_money`,
    `fmt_qty`. `fit_table()` is `render_table` made to fit the width.

Nothing here touches machine output: `--json`, the files under work/ and
reports/ that stages and users parse, and the run's marker lines keep
their exact shapes (the doc lists them).
"""

import contextlib
import os
import re
import shutil
import sys
import textwrap
from typing import Iterable, List, Optional, Sequence, Tuple

from taxjson.lib.report_model import fmt_money, fmt_qty, render_table

__all__ = [
    "WIDTH", "width", "wrap", "fill", "message", "emit", "note", "warn",
    "attention", "error", "fail", "fit_table", "records", "kv_lines",
    "relpath", "Doc", "lint", "Verbatim", "unwrapped", "fmt_money", "fmt_qty",
    "printable", "shown", "label", "relabel", "labelled", "exit_text",
    "LABELS", "console_lint", "CONSOLE_LINE_RE", "show", "show_blocks",
    "join_blocks", "settle", "settling_streams", "real_stream",
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
# output `taxjson checklist` reads. Those are never wrapped, so their
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
#     the .sum DIAGNOSTICS fold in, the text `taxjson checklist` and the
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
    "generic importer": "Generic importer",
    "schema": "Row check",
    "dedup": "Duplicates",
    "transfer-in": "Transfer-in",
    "unapplied basis adjustment": "Unapplied basis adjustment",
    "wash sale reaches a filed year": "Wash sale reaches a filed year",
}
_TOPIC_RE = re.compile(r"(?P<topic>[a-z][a-z -]*?): ")


def shown_topic(text: str) -> str:
    """An ATTENTION message's text as a person is shown it, after its
    `Warning: ` label: the `ATTENTION: ` word dropped, a known topic
    capitalised (ATTENTION_TOPICS). Display only."""
    if text.startswith("ATTENTION: "):
        text = text[len("ATTENTION: "):]
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
# name (`taxjson-gains`, `taxjson run`, `tjs sum`), then a kind word in
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
    out = []
    for part in (head + text).split("\n"):
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
    """The stream a settling_streams() proxy writes to (else `stream`)."""
    return getattr(stream, "_tj_inner", stream)


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
        for ln in lines:
            print(ln, file=file)
        return
    if cont:
        _unowe(file)
    else:
        settle(file)
    for ln in lines:
        print(ln, file=file)
    if cont or _entry_len(lines) > 1:
        _owe(file)


def show_blocks(blocks: Iterable[Sequence[str]], file=None) -> None:
    """show() each entry in turn."""
    for b in blocks:
        show(b, file)


class _Settling:
    """A text stream that writes the blank line its destination owes
    (show()) before the next text written to it. A write that is itself
    only newlines (a program's own blank line) pays the debt instead."""

    def __init__(self, inner):
        self._tj_inner = inner

    def write(self, s):
        if _OWED and s and _take_owed(self._tj_inner):
            if s.strip("\n"):
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
    every write raised AttributeError."""
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
         details: Iterable[str] = (), file=None) -> None:
    """Print message(...) to `file` (stderr by default), show()n: one
    blank line after it when it spans more than one line."""
    file = sys.stderr if file is None else file
    show(message(kind, text, prog=prog, details=details, stream=file),
         file)


def note(text: str, *, prog: Optional[str] = None,
         details: Iterable[str] = (), file=None) -> None:
    emit("note", text, prog=prog, details=details, file=file)


def warn(text: str, *, prog: Optional[str] = None,
         details: Iterable[str] = (), file=None) -> None:
    emit("warning", text, prog=prog, details=details, file=file)


def attention(text: str, *, prog: Optional[str] = None,
              details: Iterable[str] = (), file=None) -> None:
    emit("attention", text, prog=prog, details=details, file=file)


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
    (width 0 — `taxjson checklist` shows a failed command's last line,
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
from taxjson.lib.report_model import auto_aligns as _auto_aligns  # noqa: E402


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
    """A table (report_model.render_table: header, `---` rule, rows, an
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
    """

    def __init__(self, title: Optional[str] = None, *,
                 width_: Optional[int] = None, stream=None):
        self.stream = stream
        self.w = width(stream) if width_ is None else width_
        self._lines: List[str] = []
        if title:
            self._lines.extend(wrap(title, self.w))

    # Each call appends lines; a section() opens with one blank line.
    def blank(self) -> "Doc":
        self._lines.append("")
        return self

    def line(self, text: str = "") -> "Doc":
        """A verbatim line (a command to copy, a pre-formatted row);
        control characters escaped when shown to a person."""
        text = printable(text) if self.w > 0 else str(text)
        self._lines.extend(text.split("\n"))
        return self

    def section(self, heading: str) -> "Doc":
        self.blank()
        self._lines.append(printable(heading) if self.w > 0 else heading)
        return self

    def para(self, text: str, indent: str = "") -> "Doc":
        self._lines.extend(wrap(text, self.w, indent, indent))
        return self

    def item(self, text: str, indent: str = "", bullet: str = "- ") -> "Doc":
        self._lines.extend(wrap(text, self.w, indent + bullet,
                                indent + " " * len(bullet)))
        return self

    def items(self, texts: Iterable[str], indent: str = "") -> "Doc":
        for t in texts:
            self.item(t, indent)
        return self

    def kv(self, pairs: Iterable[Tuple[str, str]],
           indent: str = "") -> "Doc":
        self._lines.extend(kv_lines(pairs, indent, self.w))
        return self

    def table(self, headers, body, **kw) -> "Doc":
        kw.setdefault("width_", self.w)
        self._lines.extend(fit_table(headers, body, **kw))
        return self

    def message(self, kind: str, text: str, details=(),
                prog: Optional[str] = None) -> "Doc":
        lines = message(kind, text, prog=prog, details=details,
                        width_=self.w)
        self._lines.extend(lines)
        if self.w > 0 and _entry_len(lines) > 1:
            # Shown: one blank line after a message of more than one
            # line (lines() drops it at the end, never doubles it).
            self._lines.append("")
        return self

    def lines(self) -> List[str]:
        """The lines: no leading or trailing blank line, never two blank
        lines in a row, no trailing spaces."""
        out: List[str] = []
        for ln in self._lines:
            ln = ln.rstrip()
            if not ln and (not out or not out[-1]):
                continue
            out.append(ln)
        while out and not out[-1]:
            out.pop()
        return out

    def text(self) -> str:
        return "\n".join(self.lines())

    def print(self, file=None) -> None:
        file = sys.stdout if file is None else file
        if self.w > 0:
            settle(file)
        for ln in self.lines():
            print(ln, file=file)


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


# A line of a console a person reads (`taxjson run`, a command's
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
