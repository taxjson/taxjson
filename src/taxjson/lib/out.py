"""The house style for human-facing output (docs/output-style.md).

One home for how taxjson prints to people: the wrap width, prose
paragraphs and `- ` lists with a hanging indent, aligned `label:  value`
blocks, tables that fit the width, and the diagnostic prefixes
(`note:` / `warning:` / `warning: ATTENTION:` / `error:`) with a
one-line headline and indented detail lines.

It builds on the two older helpers instead of replacing them:

  * lib/cli_diag — the GNU `<prog>: warning: ...` convention, exit codes,
    input/output errors. `message()` is the same convention, wrapped.
  * lib/report_model — `render_table` (structured cells), `fmt_money`,
    `fmt_qty`. `fit_table()` is `render_table` made to fit the width.

Nothing here touches machine output: `--json`, the files under work/ and
reports/ that stages and users parse, and the run's marker lines keep
their exact shapes (the doc lists them).
"""

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
    "relpath", "Doc", "lint", "Verbatim", "fmt_money", "fmt_qty",
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


def width(stream=None) -> int:
    """The wrap width for `stream` (stdout by default): TAXJSON_WIDTH when
    set (0 = never wrap), else min(terminal width, 100) on a terminal,
    else 100."""
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
    one line. Existing newlines start new lines (each wrapped alike)."""
    w = width(stream) if width_ is None else width_
    hang = indent if hang is None else hang
    out: List[str] = []
    first = True
    for part in str(text).split("\n"):
        lead = indent if first else hang
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
                initial_indent=lead, subsequent_indent=hang,
                break_long_words=False, break_on_hyphens=False) or [lead]
        out.extend(ln.replace(_NBSP, " ") for ln in lines)
    return out


def fill(text: str, width_: Optional[int] = None, indent: str = "",
         hang: Optional[str] = None, stream=None) -> str:
    """wrap() joined with newlines."""
    return "\n".join(wrap(text, width_, indent, hang, stream))


# ------------------------------------------------------------ messages
_KINDS = {
    "note": "note: ",
    "warning": "warning: ",
    "attention": "warning: ATTENTION: ",
    "error": "error: ",
}


def message(kind: str, text: str, *, prog: Optional[str] = None,
            details: Iterable[str] = (), width_: Optional[int] = None,
            stream=None) -> List[str]:
    """A diagnostic as lines: `[<prog>: ]<kind>: <headline>` (wrapped with
    a two-space hanging indent) and each detail as its own indented
    paragraph. `kind`: note | warning | attention (the run's ATTENTION
    channel: `warning: ATTENTION: ...`) | error.

    Keep the headline short and complete — what happened, to what — and
    put the why and the fix in `details`: a reader (and a grep for the
    marker) sees the headline line on its own."""
    if kind not in _KINDS:
        raise ValueError(f"unknown message kind {kind!r}")
    stream = sys.stderr if stream is None else stream
    head = (f"{prog}: " if prog else "") + _KINDS[kind]
    out = wrap(head + str(text).strip(), width_, "", DETAIL_INDENT, stream)
    for d in details:
        if d is None:
            continue
        d = str(d).strip()
        # A `- ` item hangs under its text, not under the dash.
        hang = DETAIL_INDENT + ("  " if d.startswith("- ") else "")
        out.extend(wrap(d, width_, DETAIL_INDENT, hang, stream))
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
_NUMERIC_RE = re.compile(r"^[+-]?\$?\d[\d,]*(\.\d+)?%?$|^[-—]$")


def _auto_aligns(headers, body) -> List[str]:
    aligns = []
    for i in range(len(headers)):
        cells = [str(r[i]) for r in body if i < len(r) and str(r[i])]
        numeric = bool(cells) and all(_NUMERIC_RE.match(c) for c in cells)
        aligns.append(">" if numeric else "<")
    return aligns


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
    body = [[("" if c is None else str(c)) for c in r] for r in body]
    foot = [[("" if c is None else str(c)) for c in r] for r in foot]
    headers = [str(h) for h in headers]
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
        """A verbatim line (a command to copy, a pre-formatted row)."""
        self._lines.extend(str(text).split("\n"))
        return self

    def section(self, heading: str) -> "Doc":
        self.blank()
        self._lines.append(heading)
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
_RETIRED_PREFIXES = re.compile(
    r"^\s*(?:[\w./-]+:\s+)?(NOTE:|Note:|WARNING:|Warning:|!!|→ |-> |\*\*\* )")


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
    row, a leading or trailing blank line, a retired prefix (NOTE:,
    WARNING:, !!, →, ***). [] when clean."""
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
        if (len(ln) > width_ and not _looks_like_table_row(ln)
                and not any(a in ln for a in allow)):
            probs.append(f"line {i}: {len(ln)} columns: {ln[:60]}...")
        if i > 1 and not ln.strip() and not lines[i - 2].strip():
            probs.append(f"line {i}: two blank lines in a row")
        if _RETIRED_PREFIXES.match(ln):
            probs.append(f"line {i}: retired prefix: {ln[:40]}")
    return probs
