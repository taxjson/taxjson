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
    pipeline of `wash-sales`, `harvest` ...): `<Label>: <headline>`
    (`Info:` / `Warning:` / `Error:`, lib/out.label) and its detail
    lines flush-left, wrapped at the house width, one blank line after
    a message of more than one line (lib/out.show).

`console_lines` is the other direction: a captured line the run echoes
to the console (an ATTENTION or UNBOOKED line from a .diag) shown to the
person at display time — its label first (`Warning: ...`, lib/out.relabel;
the ATTENTION word is the captured form's), a frequent wordy note in its
short display form (reword), continuation lines flush-left, wrapped;
the .diag keeps the captured line.
"""

import re
import sys
from bisect import bisect_left
from typing import Iterable, List, Optional

from taxjson.lib import out

__all__ = ["say", "message_lines", "console_lines", "split_message",
           "emit_line", "reword", "is_continuation"]


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
    return out.real_stream(file) not in (sys.__stderr__, sys.__stdout__)


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
    out.show(message_lines(kind, headline, details, prog=prog,
                           indent=indent, stream=file), file)


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


def _plural(n, word: str, plural: Optional[str] = None) -> str:
    n = int(n)
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _kinds(text: str) -> str:
    """`deposit x12, transfer x1` -> `12 deposits, 1 transfer`."""
    out_ = []
    for part in text.split(", "):
        m = re.fullmatch(r"(.+?) x(\d+)", part.strip())
        out_.append(_plural(m.group(2), m.group(1)) if m else part)
    return ", ".join(out_)


# The display form of frequent wordy stage lines (parser notes a person
# reads on every run): (captured-line pattern, builder of [line in the
# captured form, detail ...]). Display only — the .diag, the .sum
# DIAGNOSTICS and every reader keep the captured line. A line no pattern
# matches is shown as it is (relabelled, wrapped). Keep the meaning and
# every action a reader must take; drop only the explanation.
_REWORD = [
    # lib/brokerages/kraken.py
    (re.compile(r"note: Kraken ledger (?P<f>.+?): (?P<n>\d+) trade "
                r"row\(s\) \((?P<t>\d+) trade\(s\)\) are booked from the "
                r"trades export beside it — every one matched\."),
     lambda m: [f"note: Kraken {m['f']}: {_plural(m['n'], 'ledger trade row')}"
                f" matched the trades export"]),
    (re.compile(r"note: Kraken ledger (?P<f>.+?): ignored (?P<n>\d+) "
                r"fiat-cash or zero-amount row\(s\) \((?P<k>[^()]*)\) — "
                r".* is not a tax event\."),
     lambda m: [f"note: Kraken {m['f']}: ignored "
                f"{_plural(m['n'], 'fiat/zero row')} ({_kinds(m['k'])}) — "
                f"not tax events"]),
    (re.compile(r"note: Kraken trades (?P<f>.+?): (?P<n>\d+) fill\(s\) "
                r"had the fee taken in the traded coin \(per the ledger\) "
                r"— .*"),
     lambda m: [f"note: Kraken {m['f']}: {_plural(m['n'], 'fill')} paid "
                f"the fee in the traded coin",
                "Booked as fewer coins received or more given; these fees "
                "are not in the fee reports."]),
    (re.compile(r"note: Kraken trades (?P<f>.+?): (?P<n>\d+) fill\(s\) "
                r"paid their fee with Kraken fee credits \(KFEE\) — .*"),
     lambda m: [f"note: Kraken {m['f']}: {_plural(m['n'], 'fill')} paid "
                f"with fee credits (KFEE), booked with no fee"]),
    (re.compile(r"note: Kraken ledger refid (?P<r>\S+) \((?P<d>[^)]*)\): "
                r"(?P<k>dust sweep|instant trade): (?P<names>.+) — "
                r"(?:a leg|(?P<n>\d+) legs) under the books' zero "
                r"\((?P<z>[^)]*)\) and worth at most (?P<v>[\d.]+ USD), "
                r"not booked: (?P<what>[^.]+)\.(?P<split> The receipt is "
                r"split over the other legs\.)?"),
     lambda m: [f"note: Kraken: {m['k']} {m['r']} ({m['d']}): "
                f"{_plural(m['n'] or 1, 'leg')} under {m['z']} not booked",
                f"{m['names']}: worth at most {m['v']}. "
                f"{m['what'][:1].upper()}{m['what'][1:]}."
                + (" The receipt is split over the other legs."
                   if m["split"] else "")]),
    # lib/brokerages/base.py emit_skip_summary (every parser)
    (re.compile(r"note: (?P<f>[^:]+): (?P<n>\d+) recognized non-event "
                r"row\(s\) not translated — (?P<l>.*?)\.?"),
     lambda m: [f"note: {m['f']}: {_plural(m['n'], 'row')} skipped (not "
                f"tax events)", m["l"] + "."]),
    # bin/taxjson_brokerage.py: the crypto-sends hint, the custody rows
    # kept aside, the per-file count.
    (re.compile(r"\s*NOTE: (?P<n>\d+) crypto withdrawal/send\(s\) among "
                r"them — if any paid for something \(payment\), .*"),
     lambda m: [f"note: {_plural(m['n'], 'crypto send')} among them: a "
                f"payment is a sale at fair value",
                "`taxjson crypto-sends` lists them (a gift is not a sale "
                "for a US donor; a move to your own wallet needs nothing)."]),
    (re.compile(r"\s*NOTE: (?P<n>\d+) crypto withdrawal/send\(s\) among "
                r"them — if any left your ownership \(gift or payment\), "
                r".*?(?P<us>; with --country usa only a payment is a "
                r"sale)?\."),
     lambda m: [f"note: {_plural(m['n'], 'crypto send')} among them: a "
                f"gift or payment is a disposition at fair value",
                "`taxjson crypto-sends` lists them (a move to your own "
                "wallet needs nothing" + ("; with --country usa only a "
                                          "payment is a sale" if m["us"]
                                          else "") + ")."]),
    (re.compile(r"\s+(?P<f>\S.*?): (?P<n>\d+) TRANSFER row\(s\) kept aside "
                r"\(custody evidence, not tax events — view with "
                r"`taxjson transfers`\)"),
     lambda m: [f"note: {m['f']}: {_plural(m['n'], 'transfer row')} kept "
                f"aside (not tax events; `taxjson transfers` lists them)"]),
    (re.compile(r"\s+(?P<f>\S.*?): (?P<n>\d+) tax objects\b(?P<r>.*)"),
     lambda m: [f"note: {m['f']}: {m['n']} tax objects{m['r']}"]),
]


# A line longer than this is shown as is: the notes _REWORD shortens are
# a few hundred characters, and a pathological line (broker data with a
# huge run of spaces) must not cost the regexes quadratic time.
_REWORD_MAX = 2000


# lib/symbol_codes' evidence for a resolved code, shortened for the
# console (the captured note keeps it whole).
_CODE_EVIDENCE = (
    (re.compile(r"paired with the (?P<b>.+?) transfer out of (?P<q>\S+) "
                r"on (?P<d>\S+), account (?P<a>.+)"),
     "{b} transfer out of {q} on {d}, {a}"),
    (re.compile(r"name match: (?P<n>.+) on (?P<b>.+?) rows of account "
                r"(?P<a>.+)"),
     "same name as {n} in {a}"),
)


def _codes_note(line: str) -> Optional[List[str]]:
    """lib/symbol_codes' one-line note of the Questrade internal codes
    an account's books carry under a ticker, as a person reads it: its
    head and count, then one detail line per code (`X000001 → QZM.US
    (...)`) and the override. None when `line` is not that note (or no
    entry parses: shown as it is)."""
    from taxjson.lib.symbol_codes import NOTE_HEAD, parse_codes_note
    if not line.startswith(NOTE_HEAD):
        return None
    codes = parse_codes_note(line)
    if not codes:
        return None
    out = [f"{NOTE_HEAD} ({len(codes)}):"]
    for code, (listing, evidence) in codes.items():
        for rx, fmt in _CODE_EVIDENCE:
            m = rx.fullmatch(evidence)
            if m:
                evidence = fmt.format(**m.groupdict())
                break
        out.append(f"{code} → {listing} ({evidence})")
    out.append("Inferred from your exports (`taxjson transfers` "
               "lists them); a ticker.map GLOBAL line for a code "
               "overrides it.")
    return out


def reword(line: str) -> List[str]:
    """A captured stage line's display form: [line, detail ...] — a
    frequent wordy note shortened (_REWORD), else [line]. Display only."""
    codes = _codes_note(line)
    if codes is not None:
        return codes
    if len(line) > _REWORD_MAX:
        return [line]
    for rx, build in _REWORD:
        m = rx.fullmatch(line)
        if m:
            return build(m)
    return [line]


# A line that starts with a person's label (relabelled).
_LABELLED = re.compile(r"(?:Info|Warning|Error): ")


def is_continuation(line: str) -> bool:
    """True when a captured stage line continues the message above it
    (an indented line that is not itself a message once shown — a
    parser's indented `  x.csv: N tax objects` count is one)."""
    if not line[:1].isspace():
        return False
    first = reword(out.printable(line))[0]
    return (first[:1].isspace()
            and not _LABELLED.match(out.relabel(first).strip()))


def console_lines(line: str, indent: str = "", stream=None,
                  width_: Optional[int] = None,
                  source: bool = True) -> List[str]:
    """A captured stage line as the run shows it under `indent`: as is
    when nothing wraps (width 0); else in its display form (reword: a
    frequent wordy note shortened), with its label first (lib/out.relabel:
    `warning: ATTENTION: ...` -> `Warning: ...`, an old `NOTE:` ->
    `Info:`; `source` keeps the program name a line carried after the
    label, False drops it), at `indent` whatever indentation it had; a
    long one becomes its headline and a detail paragraph. A line that
    is not a message (is_continuation: an indented continuation of the
    one above) and every detail line is shown at `indent` too — flush-
    left on the run's console. Display only — the .diag keeps the one
    line. Print the lines with lib/out.show (cont=is_continuation(line))
    so a message of more than one line is followed by a blank line."""
    w = out.width(stream if stream is not None else sys.stdout) \
        if width_ is None else width_
    if w <= 0:
        return [indent + line]
    # Shown to a person: a control character from broker data (an ESC
    # sequence) is shown escaped, never sent to the terminal; the label
    # starts the line.
    shown = reword(out.printable(line))
    body = out.relabel(shown[0], source=source).strip()
    if shown[0][:1].isspace() and not _LABELLED.match(body):
        # The continuation of the message above.
        lines = out.wrap(body, w, indent, indent)
    elif len(indent + body) <= w:
        lines = [indent + body]
    else:
        head, rest = split_message(body)
        lines = out.wrap(head, w, indent, indent)
        if rest:
            lines += out.wrap(rest, w, indent, indent)
    for d in shown[1:]:
        lines += out.wrap(d, w, indent, indent)
    return lines


def emit_line(text: str, *, file=None, indent: str = "") -> None:
    """Print a program's own one-line message (`warning: ...`, `note:
    ...`, `<prog>: error: ...`): as is when captured (_captured — the
    .diag keeps its bytes), else as console_lines shows it: the label
    first (`Warning: ...`, `Info: ...`; the program's own name dropped),
    the headline and its detail flush-left, wrapped, one blank line
    after it when it spans more than one line (lib/out.show). Each line
    of a multi-line `text` is shown so; an indented one continues the
    message above it."""
    file = sys.stderr if file is None else file
    if _captured(file):
        print(text, file=file)
        return
    blocks: List[List[str]] = []
    for part in str(text).split("\n"):
        lines = console_lines(part, indent, stream=file, source=False)
        if blocks and is_continuation(part):
            blocks[-1].extend(lines)
        else:
            blocks.append(lines)
    out.show_blocks(blocks, file)
