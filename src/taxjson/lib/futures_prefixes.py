"""taxjson's futures symbol prefixes, their one home: `F:ZZQZ6.US`,
`/ES`, `\\ES` (the contract size is never in the symbol). A dependency-free
leaf, so any module reads it at import (tests/test_one_copy.py holds every
module to it: a tuple, a set, a list or a regex of them is spelled here).

The parse writes `/` and `\\` as `F:` (lib/brokerages/generic.py,
lib/option_symbols.py `_FUTURES_PREFIX_RE`), so a book's future is
`F:`-prefixed; a parser's output and a .tt line may hold any of the three."""
import re

FUTURES_PREFIXES = ("F:", "/", "\\")

# The prefixes as a regex group, `(?:F:|/|\\)`, for the patterns that take
# one (an option on a future, a futures root).
FUTURES_PREFIX_GROUP = "(?:" + "|".join(re.escape(p) for p in FUTURES_PREFIXES) + ")"

# A symbol's leading prefix: `.match` tells a future, `.sub("F:", s)` writes
# its book spelling (engine/us/terms.py reads it as `_FUTURES_PREFIX_RE`).
FUTURES_PREFIX_RE = re.compile("^" + FUTURES_PREFIX_GROUP)
