"""An option symbol read: the OCC form and the brokers' own (its
underlying, expiry, right, strike), what an exercise delivers, whether
an option's root names a stock. The parsers, the engine and the views
share these; they read no book or setting and import nothing of lib but
the futures prefixes (step 8 of the architecture plan: out of lib/core,
which every parser imported for them). The contract's size, which reads
ticker.map, is lib/contract_size's."""

from __future__ import annotations

import functools
import re
from datetime import datetime
from typing import Any, Optional

from taxjson.lib.futures_prefixes import FUTURES_PREFIX_GROUP, FUTURES_PREFIX_RE


# OCC option-symbol pattern: [F:|/|\]<base><yymmdd><C|P><strike-8d>[.<ext>]
# e.g. "SAMPLG250120C00150000.US", "ABC271217P00029000.TO", or
# "F:SAMPLX271116P00040000.US" for futures options.
#
# Single regex covering every option-detection / underlying-extraction
# need across the codebase:
#   - optional futures prefix (F: or / or \)
#   - non-greedy base ticker so the OCC date/strike block consumes greedily
#   - 6-digit yymmdd, C or P, 8-digit strike-×1000
#   - optional market suffix (.TO/.US/.AX/.L)
#   - end-anchored — rejects symbols with trailing junk
#
# Centralized so the Canada engine, US engine, and ticker-map helpers
# can't drift. Sites that previously used the bare `re.search(r'\d{6}[CP]\d+', ...)`
# detector now go through `is_option_symbol` for consistency — the
# anchoring change is safe because real OCC symbols never have trailing
# junk.
_OCC_OPTION_RE = re.compile(
    r'^(' + FUTURES_PREFIX_GROUP + r'?'  # optional futures prefix (captured)
    r'[A-Z0-9\.]{1,10}?)'           # base ticker (non-greedy)
    r'(\d{6}[CP]\d+)'               # OCC contract block
    r'(?:\.([A-Z]+))?'              # optional market suffix
    r'$'
)


def _occ_match(symbol: Any) -> Optional[re.Match[str]]:
    """The OCC match of `symbol` in any letter case (a parser's own
    `zyab` cell built a contract `zyab240119C00010000.TO` that was read
    as no option; backlog TJ-4601): an ASCII symbol is matched upper-cased,
    so a lower-case one reads as the same contract."""
    s = str(symbol or '')
    # One match per spelling (the walk, the router and the radar read the
    # same symbols millions of times a run: backlog TJ-4979). A match is
    # immutable and reads nothing but its string; a `str` subclass's own
    # hash or equality stays out of the cache.
    return _occ_match_text(s) if type(s) is str else _occ_match_uncached(s)


def _occ_match_uncached(s: str) -> Optional[re.Match[str]]:
    return _OCC_OPTION_RE.match(s.upper() if s.isascii() else s)


@functools.lru_cache(maxsize=16384)
def _occ_match_text(s: str) -> Optional[re.Match[str]]:
    return _occ_match_uncached(s)


def parse_option_underlying(symbol: str) -> Optional[str]:
    """Return the underlying ticker (with market suffix) for an OCC
    option symbol, or None when `symbol` doesn't look like one. The
    futures prefix (`F:`/`/`/`\\`) IS preserved — so e.g.
    `F:SAMPLX250120P00053000.US` → `F:SAMPLX.US`, keeping futures positions
    distinct from same-base equity positions in by-ticker buckets."""
    if not symbol:
        return None
    m = _occ_match(symbol)
    if not m:
        return None
    ext = m.group(3)
    return f"{m.group(1)}.{ext}" if ext else m.group(1)


def is_option_symbol(symbol: str) -> bool:
    """True if `symbol` is an OCC-format option ticker, in any letter
    case. Accepts the `F:`/`/`/`\\` futures prefix and optional market
    suffix."""
    return bool(symbol) and bool(_occ_match(symbol))


def parse_option_expiry(symbol: str) -> Optional[str]:
    """ISO expiry date ('YYYY-MM-DD') from an OCC option symbol, or None
    when `symbol` isn't one (or carries an impossible date). The OCC
    block's first six digits are yymmdd; yy pivots into 20yy — valid
    until 2099, same horizon as every other OCC consumer here."""
    if not symbol:
        return None
    m = _occ_match(symbol)
    if not m:
        return None
    return _expiry_iso(m.group(2)[:6])


@functools.lru_cache(maxsize=16384)
def _expiry_iso(yymmdd: str) -> Optional[str]:
    """The ISO date of an OCC block's six digits (None: no such date)."""
    try:
        return datetime.strptime(yymmdd, '%y%m%d').strftime('%Y-%m-%d')
    except ValueError:
        return None


def parse_option_right(symbol: str) -> Optional[str]:
    """'C' or 'P' for an OCC option symbol, else None."""
    m = _occ_match(symbol)
    return m.group(2)[6] if m else None


def parse_option_strike(symbol: str) -> Optional[float]:
    """Strike price from an OCC option symbol (the 8-digit block is the
    strike x 1000), else None."""
    m = _occ_match(symbol)
    return int(m.group(2)[7:]) / 1000.0 if m else None


def exercise_target(tx: Any) -> str:
    """The share listing a warrant/right exercise leg delivers: an ASSIGN
    row the parser marked with `exercise_of` ('' for anything else). The
    engines treat it exactly like a long call's exercise — no
    disposition; its cost rolls into the shares (tax-logic CA-OPT-09 /
    US-OPT-06)."""
    if getattr(tx, 'action', '') != 'ASSIGN':
        return ''
    tgt = (getattr(tx, 'exercise_of', '') or '').strip()
    if not tgt or is_option_symbol(getattr(tx, 'symbol', '') or ''):
        return ''
    return tgt


_FUTURES_MONTH_RE = r'[FGHJKMNQUVXZ]\d{1,2}'
_FUTURES_PREFIX_RE = FUTURES_PREFIX_RE


def root_matches_stock(root_base: str, stock_base: str) -> bool:
    """Does an option ROOT name this stock line (the parsers' base class,
    the planner, the export and the handoff read it)? Montreal / OCC roots
    drop the share class ('SAMPLD' for SAMPLD.B, 'SAMPLCB' or 'SAMPLC' for SAMPLC.B)
    and OCC-adjusted roots carry a digit ('SAMPLE1' after a corporate
    action). Futures roots name the contract family ('F:CL' for the
    dated 'F:CLG6')."""
    if root_base == stock_base:
        return True
    if _FUTURES_PREFIX_RE.match(root_base):
        return bool(re.fullmatch(re.escape(root_base) + _FUTURES_MONTH_RE,
                                 stock_base))
    if _FUTURES_PREFIX_RE.match(stock_base):
        return False
    if (root_base.replace('.', '').replace('-', '')
            == stock_base.replace('.', '').replace('-', '')):
        return True
    head = re.split(r'[.\-]', stock_base)[0]
    if head != stock_base and root_base == head:
        return True
    r_nodigit = root_base.rstrip('0123456789')
    return r_nodigit != root_base and r_nodigit in (stock_base, head)


