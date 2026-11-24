"""taxjson's futures symbol prefixes, their one home: `F:ZZQZ6.US`,
`/ES`, `\\ES` (the contract size is never in the symbol). A dependency-free
leaf, so any module reads it at import (tests/test_one_copy.py holds every
module to it: a tuple, a set, a list or a regex of them is spelled here,
and the fold of `/` and `\\` to `F:` is `canonical_futures_symbol`).

Every symbol the books hold spells a future `F:`: the parsers write it
(lib/brokerages/generic.py rewrites a cell's `/` and `\\`), and every
reader of a symbol a person writes (a .tt line, a ticker.map or tobase.map
line, a holdings file, a command's SYMBOL) reads `/` and `\\` as `F:`
through canonical_futures_symbol (backlog TJ-5150): one contract, one
symbol, one pool."""
import re
from typing import overload

FUTURES_PREFIXES = ("F:", "/", "\\")

# The prefix the books spell a future with (every parser writes it).
BOOK_FUTURES_PREFIX = FUTURES_PREFIXES[0]

# The prefixes as a regex group, `(?:F:|/|\\)`, for the patterns that take
# one (an option on a future, a futures root).
FUTURES_PREFIX_GROUP = "(?:" + "|".join(re.escape(p) for p in FUTURES_PREFIXES) + ")"

# A symbol's leading prefix: `.match` tells a future (engine/us/terms.py
# reads it as `_FUTURES_PREFIX_RE`); canonical_futures_symbol writes its
# book spelling.
FUTURES_PREFIX_RE = re.compile("^" + FUTURES_PREFIX_GROUP)


class FuturesSymbolError(ValueError):
    """A symbol written with a `/` or `\\` prefix that names no contract
    once folded: another prefix after it (`/F:QZM26.US`, `//QZM26.US`) or
    a listing suffix alone (`/.US`). A ValueError: each reader names the
    line it was on, as it does for its other refusals (backlog TJ-5150)."""


@overload
def canonical_futures_symbol(symbol: str) -> str: ...
@overload
def canonical_futures_symbol(symbol: object) -> object: ...


def canonical_futures_symbol(symbol: object) -> object:
    """`symbol` as the books spell a future: a leading `/` or `\\` written
    `F:` (`/ZZQZ6` is `F:ZZQZ6`; an option on a future,
    `\\QZ260620C00010000.US`, is `F:QZ260620C00010000.US`: the prefix
    leads the option symbol, as its underlying's, lib/option_symbols).
    Anything else is returned as written: an `F:` symbol, a share, a
    symbol with a `/` or `\\` after its first character (`ZZB/U.TO`), a
    prefix with nothing after it, a value that is not text. The case is
    the caller's (a reader upper-cases first). A `/` or `\\` symbol whose
    rest is another prefix or a suffix alone is refused
    (FuturesSymbolError): `F:F:QZM26.US` or `F:.US` would be a pool of
    its own."""
    if not isinstance(symbol, str):
        return symbol
    m = FUTURES_PREFIX_RE.match(symbol)
    if m is None or m.group(0) == BOOK_FUTURES_PREFIX:
        return symbol
    rest = symbol[m.end():].lstrip()
    if not rest:
        return symbol
    if FUTURES_PREFIX_RE.match(rest) or rest.startswith("."):
        root = rest
        while FUTURES_PREFIX_RE.match(root):
            root = FUTURES_PREFIX_RE.sub("", root, count=1).lstrip()
        fix = (f" (write it {BOOK_FUTURES_PREFIX + root!r})"
               if root and not root.startswith(".") else "")
        what = ("another futures prefix" if not rest.startswith(".")
                else "a listing suffix and no contract")
        raise FuturesSymbolError(
            f"{symbol!r} is not a futures symbol: its {m.group(0)!r} prefix "
            f"is followed by {what}{fix}")
    return BOOK_FUTURES_PREFIX + rest
