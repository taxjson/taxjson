"""Stage messages in the house style (docs/output-style.md), for the
pipeline stages `taxjson run` captures and for the run's own console.

A stage (a parser, merge2, the gains engine, corp-actions ...) prints its
diagnostics to stderr. Under `taxjson run` that stderr is captured,
unwrapped (lib/out.unwrapped, TAXJSON_WIDTH=0), into work/*.diag — the
text the .sum DIAGNOSTICS fold in and users diff across runs and years.
Those bytes never change in a style pass. So a stage message has two
forms:

  * captured (width 0): `legacy`, the exact one-line text the .diag has
    always held;
  * shown to a person (width > 0: a stage run by hand, the in-process
    pipeline of `wash-sales`, `harvest` ...): `<kind>: <headline>` and
    indented detail lines, wrapped at the house width.

`console_lines` is the other direction: a captured line the run echoes
to the console (an ATTENTION or UNBOOKED line from a .diag) wrapped for
the person at display time, its marker first line unchanged.
"""

import sys
from typing import Iterable, List, Optional

from taxjson.lib import out

__all__ = ["say", "message_lines", "console_lines", "split_message",
           "emit_line"]


def message_lines(kind: str, text: str, details: Iterable[str] = (), *,
                  prog: Optional[str] = None, indent: str = "",
                  stream=None) -> List[str]:
    """out.message(...) with every line under `indent` (a message nested
    in the run's per-account block), fitted to the width."""
    stream = sys.stderr if stream is None else stream
    w = out.width(stream)
    if w > 0 and indent:
        w = max(out.MIN_WIDTH, w - len(indent))
    lines = out.message(kind, text, prog=prog, details=details, width_=w,
                        stream=stream)
    return [(indent + ln) if ln else ln for ln in lines]


def _captured(file) -> bool:
    """True when `file` is output captured for a program: nothing wraps
    (width 0 — lib/out.unwrapped, TAXJSON_WIDTH=0), or the stream is not
    the process's own stderr/stdout (an in-process caller redirected it
    into a buffer it reads, line by line)."""
    if out.width(file) <= 0:
        return True
    return file not in (sys.__stderr__, sys.__stdout__)


def say(kind: str, headline: str, details: Iterable[str] = (), *,
        legacy: Optional[str] = None, prog: Optional[str] = None,
        indent: str = "", file=None) -> None:
    """Print a stage message: `legacy` (when given) as is while the
    output is captured for a program (_captured: a .diag keeps its
    bytes, an in-process reader its lines), else `kind: headline` with
    the indented `details`."""
    file = sys.stderr if file is None else file
    if legacy is not None and _captured(file):
        print(legacy, file=file)
        return
    for ln in message_lines(kind, headline, details, prog=prog,
                            indent=indent, stream=file):
        print(ln, file=file)


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
    for brk in _BREAKS:
        i = text.find(brk, _MIN_HEAD)
        # A break inside a `code span` is no break.
        while i >= 0 and text[:i].count("`") % 2:
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
    if rest[0].islower() and brk != "; ":
        rest = rest[0].upper() + rest[1:]
    return head, rest


_RETIRED = (("NOTE: ", "note: "), ("Note: ", "note: "),
            ("WARNING: ", "warning: "), ("Warning: ", "warning: "))


def console_lines(line: str, indent: str = "  ", stream=None,
                  width_: Optional[int] = None) -> List[str]:
    """A captured stage line as the run shows it under `indent`: as is
    when it fits (or nothing wraps); else a marker line (`warning:
    ATTENTION: <topic>: ...`) becomes its headline and an indented
    detail paragraph, an indented continuation line wraps under its own
    indentation; a retired `NOTE:` / `WARNING:` prefix is shown lower
    case. Display only — the .diag keeps the one line."""
    w = out.width(stream if stream is not None else sys.stdout) \
        if width_ is None else width_
    if w <= 0:
        return [indent + line]
    # Shown to a person: a control character from broker data (an ESC
    # sequence) is shown escaped, never sent to the terminal.
    line = out.printable(line)
    # A retired prefix (`NOTE:`, `WARNING:`) the captured text keeps is
    # shown in the house's lower case.
    body = line.lstrip()
    own = line[:len(line) - len(body)]
    for old, new in _RETIRED:
        if body.startswith(old):
            line = own + new + body[len(old):]
            break
    shown = indent + line
    if len(shown) <= w:
        return [shown]
    if own:
        lead = indent + own
        return out.wrap(line.strip(), w, lead, lead + "  ")
    head, rest = split_message(line.strip())
    lines = out.wrap(head, w, indent, indent + out.DETAIL_INDENT)
    if rest:
        lines += out.wrap(rest, w, indent + out.DETAIL_INDENT,
                          indent + out.DETAIL_INDENT)
    return lines


def emit_line(text: str, *, file=None, indent: str = "") -> None:
    """Print a stage's one-line message (`warning: ...`, `note: ...`):
    as is when captured (_captured — the .diag keeps its bytes), else as
    console_lines shows it: the marker headline and an indented detail,
    wrapped. Each line of a multi-line `text` is shown so."""
    file = sys.stderr if file is None else file
    if _captured(file):
        print(text, file=file)
        return
    for part in str(text).split("\n"):
        for ln in console_lines(part, indent, stream=file):
            print(ln, file=file)
