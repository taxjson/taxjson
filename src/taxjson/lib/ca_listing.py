"""Canadian listing identity: the one spelling of a Canadian listing in
the books (ROOT.TO, a dotted preferred series), which every parser
(lib/brokerages/base), the .tt reader and a ticker.map EXTRACT target
(lib/ticker_map.extract_symbol) give a symbol, and `book_listing`, the
spelling of a symbol a ticker.map line names. A leaf below them: it
reads only the shipped market data (lib/markets_data) and the futures
prefixes (lib/futures_prefixes), so the ticker.map reader can use it
(out of lib/brokerages/base for that, backlog TJ-4604)."""
import re
from typing import Optional

from taxjson.lib.futures_prefixes import canonical_futures_symbol
from taxjson.lib.markets_data import (canadian_suffixes as _ca_sfx,
                                      known_suffixes as _known_sfx,
                                      listing_suffix_re as _sfx_re)


# Canadian listing identity (audit S010-05 / S014-07). A security's
# symbol is its ACB pool and superficial-loss key, so every parser must
# spell one Canadian listing the same way. The canonical form is
# ROOT.TO for EVERY Canadian venue (TSX, TSX Venture, CSE, NEO):
#   * IB, RBC and Webull cannot (RBC/Webull) or do not (IB) put the venue
#     in the symbol -- they stamp every CAD listing .TO, and real books
#     (ticker.map TOBASE rules, QUOTE price aliases) are keyed on
#     that; Questrade alone named the venue (.VN/.CN/.NE), so a Venture
#     name bought at Questrade and sold at IB split into two pools and a
#     cross-account superficial loss was missed.
#   * TSX and TSX Venture share one symbol namespace (TMX), so ROOT.TO is
#     unambiguous for a Venture listing; price lookups that need the venue
#     go through a ticker.map QUOTE line (SAMPLY.TO -> SAMPLY.V).
# TSX preferred shares are dotted per series: Questrade's FTN.PRA.TO is
# the FTN.PR.A.TO every other parser emits.
# Every Canadian venue suffix but .TO (the canonical one) and .V (a
# class letter outside CAD): the market data's venue table.
_CA_VENUE_SUFFIX_RE = _sfx_re(_ca_sfx() - {'TO', 'V'})
_CA_PREF_UNDOTTED_RE = re.compile(r'^([A-Z0-9]+)\.(PR|PF)([A-Z]{1,2})$',
                                  re.IGNORECASE)
# A fully dotted preferred series with no venue (FTN.PR.L, FTN.PR.V): its
# last letters are the SERIES, not a London (.L) or Venture (.V) suffix,
# unless they spell a listing suffix of more than one letter (FTN.PR.CN
# is the CSE listing of FTN.PR, FTN.PR.US its US one). Stripped as a
# venue, two series became one pool (backlog TJ-0970); kept as a series,
# an explicit listing got a second suffix (backlog TJ-1202).
_CA_PREF_DOTTED_RE = re.compile(r'^[A-Z0-9]+\.(?:PR|PF)\.([A-Z]{1,2})$',
                                re.IGNORECASE)


def is_ca_pref_series(symbol: str) -> bool:
    """True when `symbol` is a dotted preferred series with no venue
    suffix (FTN.PR.L, FTN.PR.V): its final letters must not be read as
    a listing suffix."""
    m = _CA_PREF_DOTTED_RE.match((symbol or '').strip())
    if not m:
        return False
    return not any(len(s) > 1 and s.upper() == m.group(1).upper()
                   for s in _known_sfx())


def canonical_ca_root(root: str) -> str:
    """Dot a TSX preferred-share series (FTN.PRA -> FTN.PR.A,
    SAMPMO.PFB -> SAMPMO.PF.B); any other root is returned unchanged. `root` is
    the symbol WITHOUT its .TO suffix."""
    m = _CA_PREF_UNDOTTED_RE.match(root or '')
    if not m:
        return root
    return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"


def canonical_ca_listing(symbol: str, currency: str = 'CAD') -> Optional[str]:
    """The canonical spelling of a symbol that names a CANADIAN venue
    (.TO, .V in CAD, .VN, .CN, .NE): ROOT.TO with a dotted preferred
    series. None when the symbol carries no Canadian venue suffix (the
    caller then suffixes it from the currency). A bare `.V` counts as
    TSX Venture only in CAD -- in another currency it may be a class
    letter."""
    sym = (symbol or '').strip()
    up = sym.upper()
    if is_ca_pref_series(sym):
        return None
    if up.endswith('.TO') and len(up) > 3:
        return f"{canonical_ca_root(sym[:-3])}.TO"
    if (up.endswith('.V') and len(up) > 2
            and (currency or '').upper() == 'CAD'):
        return f"{canonical_ca_root(sym[:-2])}.TO"
    m = _CA_VENUE_SUFFIX_RE.search(sym)
    if m and m.start() > 0:
        return f"{canonical_ca_root(sym[:m.start()])}.TO"
    return None


def book_listing(symbol: str) -> str:
    """A symbol a ticker.map line names, as the books spell it: upper
    case, a listing on a Canadian venue ROOT.TO with a dotted preferred
    series (`canonical_ca_listing` with no currency: `.CN`, `.NE`, `.VN`
    and an undotted series; a `.V` stays, a class letter outside CAD),
    a future's `/` or `\\` prefix `F:` (lib/futures_prefixes
    canonical_futures_symbol, as the .tt reader and every parser spell
    it). A line naming ZYAB.NE named a pool no parser or .tt row books
    (backlog TJ-4604, TJ-4607); one naming `/QZ.US`, the .tt line's
    spelling, kept matching it apart from the brokers' `F:QZ.US`
    (backlog TJ-5150)."""
    s = canonical_futures_symbol((symbol or '').strip().upper())
    return canonical_ca_listing(s, '') or s
