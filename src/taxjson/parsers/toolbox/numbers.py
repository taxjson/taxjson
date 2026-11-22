"""The strict number and money parsers every parser reads a cell with (step
5a of the engine refactor): `parse_strict_number` for a required cell
(`BrokerageParseError` on anything ambiguous), `desc_number` for a number
captured from description text, `check_comma_grouping` behind both, and
`strict_money` for the crypto exports' cells (`ValueError`, blank is 0; the
same thousands-grouping rule, `_THOUSANDS_COMMA_RE`, as the equity cells').
Moved as they stood from `lib/brokerages/base.py` and `_crypto_common.py`,
which re-export them; their grammars are not one yet (PLAN.md, step 5a)."""
import math
import re
from typing import Optional

from taxjson.parsers.toolbox.errors import BrokerageParseError


# Strict number grammar for REQUIRED money/quantity cells. A leading
# sign, then either a plain digit run or a comma-grouped integer part
# whose groups are exactly three digits after a lead that is not 0
# (1,234,567; "0,125" is refused), then an optional fraction. A decimal comma ("1234,56"), a space-grouped number
# ("1 000"), a stray letter, or an empty cell is not guessed at.
_STRICT_NUM_RE = re.compile(
    r'^(?P<sign>[+-]?)'
    r'(?P<int>[1-9]\d{0,2}(?:,\d{3})+|\d*)'
    r'(?P<frac>\.\d*)?'
    r'(?P<exp>[eE][+-]?\d+)?$', re.ASCII)   # ASCII digits only (S055-09)

# A comma is only ever a THOUSANDS separator: digit groups of exactly
# three after a 1-3 digit lead that does not start with 0 ("0,125" is a
# decimal comma, never 125 -- audit S055-08), optionally followed by a
# dot fraction.
# Anything else with a comma ("0,95", "1,5", "1.234,56", "12,3456") is a
# decimal-comma (French/European locale) number, and stripping the comma
# reads it 100x or 10x too large -- so it is refused, never scaled.
_THOUSANDS_COMMA_RE = re.compile(
    r'^[+-]?[1-9]\d{0,2}(?:,\d{3})+(?:\.\d*)?$', re.ASCII)   # ASCII digits only (S055-09)

def check_comma_grouping(num_text: str, raw=None, *, where: str = '',
                         field: str = 'value') -> None:
    """Raise BrokerageParseError when `num_text` (a number already
    stripped of currency signs and accounting parentheses) contains a
    comma that is not a valid thousands separator. `raw` is the original
    cell for the message. '1,234.56' passes; '1234,56' and '1.234,56'
    raise -- a decimal comma is refused rather than read as 123456."""
    if ',' not in num_text:
        return
    if _THOUSANDS_COMMA_RE.match(num_text.strip()):
        return
    loc = f"{where}: " if where else ''
    shown = num_text if raw is None else raw
    raise BrokerageParseError(
        f"{loc}{field} {shown!r} uses a comma that is not a thousands "
        f"separator — a decimal comma (French/European locale)? It is "
        f"refused rather than read 10x-100x too large. Re-export with a "
        f"decimal POINT (e.g. 1234.56 or 1,234.56).")

def desc_number(text: str, *, where: str = '', field: str = 'value',
                strict: bool = True) -> Optional[float]:
    """A number captured from description text (audit S062-13 / S064-19 /
    S016-01): thousands commas only. A decimal comma raises
    BrokerageParseError when `strict` (money and share counts), else
    returns None (an informational field the caller leaves unset)."""
    s = (text or '').strip()
    loc = f"{where}: " if where else ''
    try:
        check_comma_grouping(s, where=where, field=field)
        try:
            val = float(s.replace(',', ''))
        except ValueError:
            # An empty or malformed capture (a regex's optional group):
            # the toolbox's refusal, never a traceback (TJ-2359).
            raise BrokerageParseError(f"{loc}{field} {s!r} is not a number") from None
        if not math.isfinite(val):
            # A digit run beyond float range overflows to inf, which a
            # book value or a split ratio would carry (TJ-2062).
            raise BrokerageParseError(
                f"{loc}{field} {s!r} is out of range — refusing it")
        return val
    except BrokerageParseError:
        if strict:
            raise
        return None

# Unicode minus signs and dashes spreadsheets substitute for '-' (the
# fullwidth one from East Asian locales included): each reads as a minus.
MINUS_CHARS = ('−', '‒', '–', '—', '﹣', '－')

_CURRENCY_SIGNS = ('$', '€', '£', '¥')

def parse_strict_number(raw, *, field: str = 'value', where: str = '',
                        allow_blank: bool = False,
                        blank: Optional[float] = None) -> Optional[float]:
    """Parse a REQUIRED numeric cell, raising BrokerageParseError on
    anything ambiguous instead of returning 0.

    Accepted: `1234.5`, `-1,234.50`, `+3`, `.5`, `1e-05`, a currency
    sign (`$-12.00`, `-$12.00`), accounting parentheses as NEGATIVE
    (`(1,234.56)` == -1234.56), and the unicode minus (U+2212) or a
    dash as the sign. Rejected: a decimal comma (`1234,56`), thousands
    separators outside valid three-digit groups (`12,34`, `1,2345`),
    space-grouped digits (`1 000`), a trailing minus, and any other
    text. A blank cell is an error unless `allow_blank`, in which case
    `blank` is returned. `field`/`where` name the cell in the error."""
    loc = f"{where}: " if where else ''
    s = '' if raw is None else str(raw).strip()
    if not s:
        if allow_blank:
            return blank
        raise BrokerageParseError(f"{loc}required {field} is blank")
    t = s
    for ch in MINUS_CHARS:
        t = t.replace(ch, '-')
    neg = False
    if t.startswith('(') and t.endswith(')'):
        neg = True
        t = t[1:-1].strip()
    # ONE currency sign on either side of the sign: $-12 / -$12. A
    # second one ('$€5', '$-€5') is text, refused below (audit A2-0633).
    for cs in _CURRENCY_SIGNS:
        if t.startswith(cs):
            t = t[len(cs):].lstrip()
            break
        if t[:1] in '+-' and t[1:].startswith(cs):
            t = t[0] + t[1 + len(cs):].lstrip()
            break
    m = _STRICT_NUM_RE.match(t)
    if (not m or not (m.group('int') or (m.group('frac') or '')[1:])
            or (m.group('exp') and ',' in m.group('int'))
            or (neg and m.group('sign'))):
        raise BrokerageParseError(
            f"{loc}{field} {s!r} is not a number this parser accepts "
            f"(decimal commas, space-grouped digits and text are refused "
            f"rather than guessed)")
    val = float(t.replace(',', ''))
    if not math.isfinite(val):
        # 1e400 or a 400-digit run overflows to inf (audit S055-09).
        raise BrokerageParseError(
            f"{loc}{field} {s!r} is out of range — refusing it")
    return -val if neg else val


# A number in description text: digits with thousands commas and an
# optional decimal part ('1,234.56', '.5'); what desc_number reads.  # pii-ok
DESC_NUMBER_RE = r'(\d+(?:,\d+)*(?:\.\d+)?|\.\d+)'



def _ctx(context: str) -> str:
    return f" ({context})" if context else ''


# Currency markers an exchange may glue to an amount. Longest first so
# `CA$` is not half-eaten by `A$`/`$`.
_MONEY_PREFIXES = ('CA$', 'US$', 'C$', 'A$', '$', '€', '£')


# What is left once sign, currency and thousands commas are stripped:
# ASCII digits with at most one decimal point, and an optional exponent
# (tiny crypto quantities: 1e-8). float() also took '1_000' and
# non-ASCII digits (R1-114).
_PLAIN_NUMBER = re.compile(r'^(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$', re.ASCII)

def strict_money(raw, what: str = 'amount', context: str = '') -> float:
    """Parse one numeric CSV cell. Empty -> 0.0. `(x)` is negative.
    Commas must be thousands separators (a European `1.234,56` is
    refused, never read as 1.23456). Raises ValueError, naming the
    field and `context`, on anything unparseable."""
    if raw is None:
        return 0.0
    s = str(raw).strip()
    if not s:
        return 0.0
    orig = s
    neg = False
    signs = 0          # at most ONE of (x), a leading -/+, a -/+ after $
    if s.startswith('(') and s.endswith(')'):
        neg = True
        signs += 1
        s = s[1:-1].strip()
    if s.startswith('-'):
        neg = not neg
        signs += 1
        s = s[1:].strip()
    elif s.startswith('+'):
        signs += 1
        s = s[1:].strip()
    for p in _MONEY_PREFIXES:
        if s.upper().startswith(p):
            s = s[len(p):].strip()
            break
    # A sign AFTER the currency marker (`CA$-4.00`, `$-4.00`).
    if s[:1] in ('-', '+'):
        neg = neg != (s[0] == '-')
        signs += 1
        s = s[1:].strip()
    if signs > 1:
        # '--5', '(-5)', '-$-5' used to cancel into +5 (R1-114).
        raise ValueError(
            f"unparseable {what} {orig!r}{_ctx(context)}: more than one "
            f"sign marker — refusing to guess the sign.")
    if ',' in s:
        if not _THOUSANDS_COMMA_RE.match(s):
            raise ValueError(
                f"unparseable {what} {orig!r}{_ctx(context)}: commas are "
                f"only accepted as thousands separators (1,234.56).")
        s = s.replace(',', '')
    if not _PLAIN_NUMBER.match(s):
        # float() also takes '1_000', non-ASCII digits and 'nan'.
        raise ValueError(
            f"unparseable {what} {orig!r}{_ctx(context)} — refusing to "
            f"book it as 0. Fix the cell or report the new export "
            f"format.")
    try:
        v = float(s)
    except ValueError:
        raise ValueError(
            f"unparseable {what} {orig!r}{_ctx(context)} — refusing to "
            f"book it as 0. Fix the cell or report the new export "
            f"format.") from None
    if v != v or v in (float('inf'), float('-inf')):
        raise ValueError(f"non-finite {what} {orig!r}{_ctx(context)}")
    return -v if neg else v
