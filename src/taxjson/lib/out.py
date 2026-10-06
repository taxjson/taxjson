"""The house style for human-facing output (docs/output-style.md).

One home for how taxjson prints to people: the wrap width, prose
paragraphs and `- ` lists with a hanging indent, aligned `label:  value`
blocks, tables that fit the width, and the message labels with a
one-line headline and indented detail lines: shown to a person,
`Info:` / `Warning:` / `Warning: ATTENTION:` / `Error:` start the line;
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
    "LABELS",
]

# Prose wraps here when stdout is not a terminal (a pipe, a file, a test)
# and at most here on a terminal.
WIDTH = 100
# Never wrap narrower than this, whatever the terminal says.
MIN_WIDTH = 40
# The hanging indent of a message's continuation and detail lines (the
# run's DIAGNOSTICS collector keeps a marker line plus the indented lines
# that follow it).
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
    min(terminal width, 100) on a terminal, else 100."""
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
    return max(MIN_WIDTH, min(cols, WIDTH))


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
    "attention": "Warning: ATTENTION: ",
    "error": "Error: ",
}


def label(kind: str, width_: Optional[int] = None, stream=None) -> str:
    """The label that starts a `kind` message (note | warning | attention
    | error) printed to `stream` (stderr by default): `Info: ` /
    `Warning: ` / `Warning: ATTENTION: ` / `Error: ` shown to a person
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
    head = LABELS[_KIND_OF[m.group("kind").lower()]]
    prog = m.group("prog")
    return (m.group("lead") + head
            + (f"{prog}: " if prog and source else "")
            + line[m.end():])


def labelled(text: str, stream=None, *, source: bool = False) -> str:
    """`text` as printed to `stream` (stdout by default): each line
    relabel()ed when shown to a person (width > 0), as is when captured
    (width 0). For a message line built as text (`note: ...`) that is
    also written where a program reads it."""
    if width(sys.stdout if stream is None else stream) <= 0:
        return str(text)
    return "\n".join(relabel(ln, source=source)
                     for ln in str(text).split("\n"))


def message(kind: str, text: str, *, prog: Optional[str] = None,
            details: Iterable[str] = (), width_: Optional[int] = None,
            stream=None) -> List[str]:
    """A diagnostic as lines: the label and a one-line headline (wrapped
    with a two-space hanging indent), then each detail as its own
    indented paragraph. `kind`: note | warning | attention (the run's
    ATTENTION channel) | error.

    Shown to a person (width > 0) the line starts with the label —
    `Info: ` / `Warning: ` / `Warning: ATTENTION: ` / `Error: ` — and
    `prog` is not shown (it is the command the person ran). Captured for
    a program (width 0) it is `[<prog>: ]<kind>: <headline>`, lower
    case, the bytes the run and the checklist read (label()).

    Keep the headline short and complete — what happened, to what — and
    put the why and the fix in `details`: a reader (and a grep for the
    marker) sees the headline line on its own."""
    if kind not in _KINDS:
        raise ValueError(f"unknown message kind {kind!r}")
    stream = sys.stderr if stream is None else stream
    w = width(stream) if width_ is None else width_
    head = LABELS[kind] if w > 0 else \
        (f"{prog}: " if prog else "") + _KINDS[kind]
    out = wrap(head + str(text).strip(), w, "", DETAIL_INDENT, stream)
    for d in details:
        if d is None:
            continue
        d = str(d).strip()
        if w > 0:
            # A relayed captured line (a stage's `<prog>: error: ...`)
            # shown with its label first, its program as the source.
            d = "\n".join(relabel(x) for x in d.split("\n"))
        # A `- ` item hangs under its text, not under the dash.
        hang = DETAIL_INDENT + ("  " if d.startswith("- ") else "")
        out.extend(wrap(d, w, DETAIL_INDENT, hang, stream))
    return out


def emit(kind: str, text: str, *, prog: Optional[str] = None,
         details: Iterable[str] = (), file=None) -> None:
    """Print message(...) to `file` (stderr by default)."""
    file = sys.stderr if file is None else file
    for ln in message(kind, text, prog=prog, details=details, stream=file):
        print(ln, file=file)


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
    for ln in lines:
        print(ln, file=sys.stderr)
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
        self._lines.extend(message(kind, text, prog=prog, details=details,
                                   width_=self.w))
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
