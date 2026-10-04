import re
from datetime import datetime

# Delegate the two OCC primitives to the canonical helpers in `core.py`.
# Previously this module carried its own near-duplicate regexes which
# could drift from the engine's view of "is this an option?" — a recipe
# for subtle classification disagreements (e.g. wash-sale vs ticker-map
# disagreeing on whether `F:CL...` counts).
from taxjson.lib.core import parse_option_underlying, is_option_symbol

get_underlying = parse_option_underlying
is_option_ticker = is_option_symbol

def is_future_ticker(symbol: str) -> bool:
    """Checks if a symbol is a future (starts with / or \\ or F:)."""
    return symbol.startswith(('/', '\\', 'F:'))

def get_base_ticker_info(symbol: str):
    """
    Extracts the base symbol and extension (currency/exchange) from a string.
    Handles TICKER.EXT or OCC strings.
    """
    parts = symbol.rsplit('.', 1)
    if len(parts) > 1:
        ext = parts[1].upper()
        full_ticker = parts[0]
    else:
        ext = ""
        full_ticker = symbol

    # Handle OCC Options (extract symbol before the date/strike block)
    # e.g., ABC.B271217C00030000 -> ABC.B
    match = re.match(r'^((?:F:|[\/\\])?[A-Z0-9\.]+?)\d{6}[CP]\d+', full_ticker, re.IGNORECASE)
    if match:
        return match.group(1), ext
    
    return full_ticker, ext

def get_option_type(symbol: str) -> str:
    """Returns 'C' or 'P' from an OCC-style option ticker."""
    match = re.search(r'\d{6}([CP])\d+', symbol, re.IGNORECASE)
    return match.group(1).upper() if match else None

def map_ticker(symbol: str, target_currency: str = "CAD") -> str:
    """
    Maps a ticker symbol to its equivalent in the target currency (usually CAD).
    Example: SHOP.US -> SHOP.TO if target is CAD.
    Also handles converting dotted option strings to OCC syntax.
    Example: ABC.17DEC27.12.P -> ABC271217P00012000
    """
    # 1. Handle Dotted Options (e.g. ABC.17DEC27.12.P)
    option_pattern = r'^([A-Z0-9]+)\.(\d{1,2}[A-Z]{3}\d{2})\.(\d+(?:\.\d+)?)\.([CP])$'
    match = re.match(option_pattern, symbol, re.IGNORECASE)
    if match:
        underlying, date_str, strike_str, opt_type = match.groups()
        try:
            # Parse date 19SEP25
            dt = datetime.strptime(date_str.upper(), '%d%b%y')
            occ_date = dt.strftime('%y%m%d')

            # Format strike: 8 digits (5 integer, 3 decimal).
            # Use Decimal to avoid float-precision truncation —
            # `int(float("4.02") * 1000)` was returning 4019 instead of
            # 4020 (and similar for ~half of all sub-$200 cent-precision
            # strikes) because float("4.02") underflows to 4.0199999…
            from decimal import Decimal
            strike_int = int(Decimal(strike_str) * 1000)
            occ_strike = f"{strike_int:08d}"
            
            # Construct OCC symbol
            symbol = f"{underlying.upper()}{occ_date}{opt_type.upper()}{occ_strike}"
        except (ValueError, TypeError):
            pass # Fall through to regular mapping if parsing fails

    if target_currency != "CAD":
        return symbol
        
    parts = symbol.rsplit('.', 1)
    if len(parts) < 2:
        return symbol
        
    base, ext = parts[0], parts[1].upper()
    
    # Simple mapping based on common extensions
    mapping = {
        'US': 'TO', # Simplified: assume US stocks map to TO for CAD tracking if not specified
        'USD': 'TO',
        'NASDAQ': 'TO',
        'NYSE': 'TO',
    }
    
    new_ext = mapping.get(ext, ext)
    return f"{base}.{new_ext}"


def class_share_aliases(symbols) -> dict:
    """{option-root listing: class share} for an option root that names
    no share listing among `symbols` but exactly ONE class share of that
    root on the same exchange: RBC books Rogers' Montreal calls under the
    root RCI (RCI271217C00030000.TO) while the shares are RCI.B.TO, and
    the per-underlying reports filed the covered-call gain under a
    phantom RCI.TO ticker (S040-11). Same rule as buy/sell-check
    (S047-01)."""
    import re
    shares = {str(s).strip().upper() for s in symbols
              if s and not is_option_symbol(str(s).strip().upper())}
    by_root: dict = {}
    for s in shares:
        m = re.fullmatch(r"(.+)\.([A-Z]{1,2})\.([A-Z]{1,3})", s)
        if m:
            by_root.setdefault(f"{m.group(1)}.{m.group(3)}", set()).add(s)
    return {r: next(iter(cs)) for r, cs in by_root.items()
            if len(cs) == 1 and r not in shares}


def underlying_of(symbol: str, aliases: dict) -> str:
    """The share listing an option (or a share) groups under: the parsed
    option underlying, mapped through class_share_aliases."""
    sym = str(symbol or "")
    if is_option_symbol(sym):
        und = parse_option_underlying(sym) or sym
        return aliases.get(und.upper(), und)
    return sym


# ---------------------------------------------------------------------------
# ticker.map side rules: the per-symbol lookups that are not renames.
#
# ticker.map is the project's ONE mapping file. Besides the rename rules
# (GLOBAL, TOBASE, JOURNAL, DELETE, DISTINCT, RENAME — parsed by
# bin/taxjson_ticker_map._parse_map_file, which hands these lines here),
# it carries four lookup keywords that change no symbol in the books:
#
#   QUOTE       SYMBOL YAHOO_SYMBOL [QTY_RATIO]
#       the Yahoo Finance spelling a price lookup uses for SYMBOL
#       (harvest, the price chain); QTY_RATIO (default 1) converts the
#       position's quantity into the quoted ticker's units (a ticker
#       consumed by a merger, quoted as the acquirer).
#   CRYPTO      SYMBOL YAHOO_ID
#       the Yahoo id of a coin whose ticker collides with another
#       asset (fill-crypto prices `<YAHOO_ID>-USD`; crypto-sends and
#       harvest quote the same id). The only coin ids there are: with no
#       line a coin is `<SYMBOL>-USD`. A trailing `-USD` on YAHOO_ID is
#       dropped (the full pair as Yahoo shows it is accepted).
#   EXTRACT     DESCRIPTION WORDS | CURRENCY | SYMBOL
#       a parser symbol-extraction override: a broker row whose
#       description contains DESCRIPTION WORDS (whole words, any case)
#       and whose currency is CURRENCY ('*' = any) gets SYMBOL — for a
#       security the currency->exchange suffix mislabels (the TSX-only
#       USD unit DLR.U.TO). First matching line wins.
#   T1135       SYMBOL COUNTRY
#       the T1135 domicile of SYMBOL where its listing suffix is wrong
#       (an interlisted company): an ISO 3166 alpha-3 code, or
#       CA/CAN/CANADA/EXCLUDE for "not specified foreign property"
#       (lib/t1135_country). Read by taxjson-t1135 / `taxjson t1135`.
#
# These used to be files of their own (yf_ticker.map,
# crypto_ticker.map, ticker_extraction_overrides.txt, t1135.map);
# `taxjson migrate` folds an old project's files into ticker.map.
#
# TRADINGVIEW SYMBOL EXCHANGE was a fourth lookup (the exchange prefix of
# the TradingView watchlist export, once tv_exchange.map). The export was
# removed: a TRADINGVIEW line left in a ticker.map is ignored — never a
# problem — and `taxjson run` says once that it can be deleted
# (SideRules.retired). `taxjson migrate` only renames an old
# tv_exchange.map (RETIRED_MAP_FILES).
# ---------------------------------------------------------------------------

TICKER_MAP_NAME = "ticker.map"
SIDE_KEYWORDS = ("QUOTE", "CRYPTO", "EXTRACT", "T1135")
# Keywords of removed features: lines carrying them are skipped by every
# reader (read_side_rules lists where they are) — {keyword: what went}.
RETIRED_KEYWORDS = {"TRADINGVIEW": "TradingView export removed"}
RENAME_KEYWORDS = ("GLOBAL", "TOBASE", "JOURNAL", "DELETE", "DISTINCT",
                   "RENAME")

# The old per-purpose files ticker.map replaced: {file: keyword}.
LEGACY_MAP_FILES = {
    "yf_ticker.map": "QUOTE",
    "crypto_ticker.map": "CRYPTO",
    "ticker_extraction_overrides.txt": "EXTRACT",
    "t1135.map": "T1135",
}
# Old per-purpose files of REMOVED features: nothing reads them, so no
# command stops for them (no rule could be silently lost); `taxjson
# migrate` renames them to <name>.migrated without converting anything.
RETIRED_MAP_FILES = {
    "tv_exchange.map": "TradingView export removed",
}

_CURRENCY_RE = re.compile(r'^(?:[A-Z]{3}|\*)$')


from taxjson.lib.cli_diag import InputContentError as _InputContentError


class LegacyMapFileError(_InputContentError):
    """An old per-purpose map file sits where ticker.map is read: its
    rules would be silently ignored. `taxjson migrate` moves them."""


class SideRules:
    """The parsed QUOTE / CRYPTO / EXTRACT / T1135 lines of one
    ticker.map. quote: {SYMBOL: (yahoo, ratio)}; crypto: {SYMBOL: yahoo
    id}; extract: [(description words lower-cased, CURRENCY, symbol)] in
    file order; t1135: {SYMBOL: ISO3 code, or "CA" for not foreign
    property} (lib/t1135_country); problems: the lines that cannot be
    read or that contradict an earlier line, as `<file>:<lineno>:
    <message>`; t1135_problems: the ones a T1135 reader must not skip —
    a T1135 line, or a line with no keyword at all (an old t1135.map
    line pasted in); retired: the `<file>:<lineno>` of each skipped line
    of a removed feature (RETIRED_KEYWORDS — a TRADINGVIEW line)."""

    def __init__(self):
        self.quote: dict = {}
        self.crypto: dict = {}
        self.extract: list = []
        self.t1135: dict = {}
        self.problems: list = []
        self.t1135_problems: list = []
        self.retired: list = []

    def empty(self) -> bool:
        return not (self.quote or self.crypto or self.extract
                    or self.t1135)


def _comment_free(raw: str) -> str:
    """A ticker.map line without its `# note` and BOM (the rename
    parser's rule)."""
    return raw.lstrip('﻿').split('#', 1)[0].strip()


def parse_side_line(kw: str, line: str):
    """(kind, key, value) of one comment-free side-rule line whose
    keyword is `kw` (upper case); raises ValueError naming what is
    wrong. kind is the lower-case keyword."""
    body = line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else ""
    if kw == "EXTRACT":
        parts = [p.strip() for p in body.split('|')]
        if len(parts) != 3 or not parts[0] or not parts[2]:
            raise ValueError(
                "EXTRACT needs `EXTRACT description words | CURRENCY | "
                "SYMBOL` (CURRENCY a 3-letter code or '*')")
        desc, cur, sym = parts
        if len(sym.split()) != 1:
            raise ValueError(f"EXTRACT symbol {sym!r} must be one word")
        cur = cur.upper()
        if not _CURRENCY_RE.match(cur):
            raise ValueError(f"EXTRACT currency {parts[1]!r} is not a "
                             f"3-letter code or '*'")
        return "extract", (desc.lower(), cur), sym
    toks = body.split()
    if kw == "QUOTE":
        if len(toks) not in (2, 3):
            raise ValueError("QUOTE needs `QUOTE SYMBOL YAHOO_SYMBOL "
                             "[QTY_RATIO]`")
        ratio = 1.0
        if len(toks) == 3:
            try:
                ratio = float(toks[2])
            except ValueError:
                ratio = float("nan")
            if not (ratio > 0 and ratio != float("inf")):
                raise ValueError(f"QUOTE ratio {toks[2]!r} must be a "
                                 f"positive number")
        return "quote", toks[0].upper(), (toks[1], ratio)
    if kw == "CRYPTO":
        if len(toks) != 2:
            raise ValueError("CRYPTO needs `CRYPTO SYMBOL YAHOO_ID`")
        # Yahoo shows the full pair (`ABC12345-USD`); the id is the part
        # before `-USD`, which the lookups append — copied whole, the
        # pair would have been quoted as `ABC12345-USD-USD`.
        yid = toks[1]
        if yid.upper().endswith("-USD") and len(yid) > 4:
            yid = yid[:-4]
        return "crypto", toks[0].upper(), yid
    if kw == "T1135":
        from taxjson.lib.t1135_country import (NOT_FOREIGN_WORDS,
                                               parse_country)
        if len(toks) != 2:
            raise ValueError(
                f"T1135 needs `T1135 SYMBOL COUNTRY` (COUNTRY an ISO 3166 "
                f"alpha-3 code, or {'/'.join(NOT_FOREIGN_WORDS)} for not "
                f"foreign property)")
        return "t1135", toks[0].upper(), parse_country(toks[1])
    raise ValueError(f"{kw} is not a ticker.map lookup keyword")


def add_side_rule(rules: "SideRules", kind: str, key, value,
                  where: str, line: str) -> None:
    """Record one parsed side rule; a second line giving the same key
    another value is a problem (one symbol, one answer)."""
    if kind == "extract":
        for (d, c, s) in rules.extract:
            if (d, c) == key and s != value:
                rules.problems.append(
                    f"{where}: EXTRACT {key[0]!r} | {key[1]} is already "
                    f"mapped to {s} by an earlier line (the first "
                    f"match wins, so this line would never apply): "
                    f"{line!r}")
                return
            if (d, c) == key:
                return
        rules.extract.append((key[0], key[1], value))
        return
    table = getattr(rules, kind)
    if key in table and table[key] != value:
        rules.problems.append(
            f"{where}: {kind.upper()} {key} is given twice with different "
            f"values ({table[key]!r} and {value!r}) — keep one: {line!r}")
        return
    table[key] = value


def read_side_rules(path) -> "SideRules":
    """The side rules of the ticker.map at `path`. A line whose first
    word is no ticker.map keyword at all is a problem too (an old
    yf_ticker.map / ticker_extraction_overrides.txt line pasted in
    without its keyword). Raises OSError/InputReadError when the file
    cannot be read."""
    from pathlib import Path
    from taxjson.lib.cli_diag import read_text_utf8
    p = Path(path)
    rules = SideRules()
    for lineno, raw in enumerate(read_text_utf8(p).splitlines(), 1):
        line = _comment_free(raw)
        if not line:
            continue
        kw = line.split()[0].upper()
        where = f"{p.name}:{lineno}"
        if kw in RENAME_KEYWORDS:
            continue
        if kw in RETIRED_KEYWORDS:
            rules.retired.append(where)
            continue
        if kw not in SIDE_KEYWORDS:
            msg = (f"{where}: line has no ticker.map keyword "
                   f"({'/'.join(RENAME_KEYWORDS + SIDE_KEYWORDS)}): "
                   f"{line!r}")
            rules.problems.append(msg)
            rules.t1135_problems.append(msg)
            continue
        try:
            kind, key, value = parse_side_line(kw, line)
        except ValueError as e:
            rules.problems.append(f"{where}: {e}: {line!r}")
            if kw == "T1135":
                rules.t1135_problems.append(rules.problems[-1])
            continue
        n = len(rules.problems)
        add_side_rule(rules, kind, key, value, where, line)
        if kind == "t1135" and len(rules.problems) > n:
            rules.t1135_problems.append(rules.problems[-1])
    return rules


def legacy_map_files(directory) -> list:
    """The old per-purpose map files present in `directory` (names)."""
    import os
    from pathlib import Path
    d = Path(directory)
    return [n for n in LEGACY_MAP_FILES if os.path.lexists(d / n)]


def refuse_legacy_map_file(directory, name: str) -> None:
    """Raise LegacyMapFileError when the old file `name` is in
    `directory`: its rules now live in ticker.map, and reading neither
    would silently drop them."""
    import os
    from pathlib import Path
    if os.path.lexists(Path(directory) / name):
        raise LegacyMapFileError(
            f"{Path(directory) / name}: {name} is no longer read — its "
            f"lines are now `{LEGACY_MAP_FILES[name]}` lines in "
            f"ticker.map. Run `taxjson migrate` in the project (preview "
            f"with --dry-run).")


def find_ticker_map(dirs):
    """The first ticker.map in `dirs` (paths), or None. Each searched
    folder holding an old per-purpose map file is refused
    (LegacyMapFileError) — never silently skipped."""
    from pathlib import Path
    for d in dirs:
        for name in LEGACY_MAP_FILES:
            refuse_legacy_map_file(d, name)
        p = Path(d) / TICKER_MAP_NAME
        if p.is_file():
            return p
    return None


def side_rules_in(dirs) -> "SideRules":
    """The side rules of the first ticker.map in `dirs` (an empty set
    when none). A line that cannot be read is skipped with a warning
    naming it (`taxjson run` refuses such a map up front)."""
    import sys
    p = find_ticker_map(dirs)
    if p is None:
        return SideRules()
    rules = read_side_rules(p)
    for msg in rules.problems:
        print(f"warning: ticker.map problem: {msg} (`taxjson run` refuses "
              f"this map)", file=sys.stderr)
    return rules
