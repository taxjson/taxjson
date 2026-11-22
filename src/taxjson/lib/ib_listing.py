"""IB's listing suffixes: the currency -> suffix table the market data
gives (lib/markets) and the pattern of the suffixes it appends, the
suffix a row's currency gives (`_ib_currency_ext`, warning once on a
currency with none), the currency/venue tags of a dotted symbol
(`_IB_CURRENCY_TAGS`) and the suffix of a stock or warrant row
(`_ib_listing_ext`: a USD unit of a TSX-listed fund is a Canadian
listing, a line on the LSE is `.L`, and an unknown venue keeps the
currency's suffix, noted once). Out of lib/brokerages/ib_extractor (the IB parser), which lib/corp_actions
called up into for them while ib_extractor imports corp_actions
(step 8 of the architecture plan: lib's import cycle)."""

import re
from typing import Any, Dict

from taxjson.lib.once import said_set
from taxjson.lib.stage_msg import emit_line
# The market data's currency -> suffix table (lib/markets), one for every
# parser; the suffixes it appends are what the IB parser strips (_IB_EXT_RE).
# The one fiat list (lib/markets): _IB_CURRENCY_TAGS.
from taxjson.lib.markets import (currency_suffixes as _cur_sfx,
                                 fiat_currencies as _fiat,
                                 listing_suffix_re as _sfx_re)


# IB currency → exchange-suffix map. Previously duplicated inline at three
# sites; an unmapped currency (EUR/JPY/CHF/...) silently produced a junk
# suffix like SYM.EUR that no downstream ticker map recognizes, fragmenting
# the position. Centralized + a one-time warning so a held exotic currency is
# surfaced instead of corrupting silently.
_IB_CURRENCY_EXT = _cur_sfx()
_IB_EXT_RE = _sfx_re(set(_IB_CURRENCY_EXT.values()))
_IB_WARNED_CURRENCIES = said_set()


def _ib_currency_ext(currency: str) -> str:
    ext = _IB_CURRENCY_EXT.get(currency)
    if ext is None:
        if currency not in _IB_WARNED_CURRENCIES:
            _IB_WARNED_CURRENCIES.add(currency)
            emit_line(f"warning: IB currency {currency!r} has no exchange-suffix "
                f"mapping; using '.{currency}', which downstream ticker maps "
                f"won't recognize (position may fragment). Add it to "
                f"[currency_suffix] in taxjson/data/markets.toml.")
        return currency
    return ext


# A dotted symbol whose last part is a currency code is an IB currency or
# venue line (ABH.CAD: the temporary line a merger fraction sat on), not
# a class share like SAMPMP.B or a unit like SAMPMQ.UN (audit R1-59 / S059-00).
# The one fiat list (lib/markets).
_IB_CURRENCY_TAGS = _fiat()


# IB's Financial Instrument Information names a listing venue (Listing
# Exch): which suffix each code is, is market data (lib/markets.
# ib_venue_suffix: taxjson/data/markets.toml [ib_venues], ticker.map
# VENUE lines). An unknown venue leaves the currency's suffix, noted
# once with the VENUE line to add.


def _ib_listing_ext(asset_cat: str, raw_symbol: str, currency: str,
                    fii: Dict[tuple, Any]) -> str:
    """The exchange suffix of a stock/warrant row: from the currency,
    except a USD-class unit of a TSX-listed fund ('ZSP.U', 'SAMPLF.U'),
    which is a Canadian listing (SAMPMD.U.TO — the spelling RBC and the
    ticker maps use); `.US` made it a fictional US security (audit
    S010-06). Only the `.U` unit class is re-suffixed: IB's instrument
    list names a single primary listing per symbol, so a USD trade of an
    interlisted ordinary share (SAMPMR on the NYSE) must keep `.US`."""
    ext = _ib_currency_ext(currency)
    s = (raw_symbol or '').strip()
    if asset_cat not in ('Stocks', 'Warrants') or ext in ('TO', 'L'):
        return ext
    info = (fii.get((asset_cat, s))
            or fii.get((asset_cat, re.sub(r'\s+', ' ', s))) or {})
    exch = (info.get('exch') or '').upper()
    from taxjson.lib.markets import (canadian_suffixes, ib_venue_suffix,
                                     note_builtin)
    venue = ib_venue_suffix(exch) if exch else None
    if exch and venue is None:
        note_builtin("ib-venue", exch, (
            f"IB listing venue {exch!r} ({s}) is not in taxjson's market "
            f"data: the line keeps its currency's suffix .{ext}. If it is "
            f"another market, add `VENUE {exch} SUFFIX` to ticker.map."))
    if re.search(r'[.\s][Uu]$', s) and venue in canadian_suffixes():
        return 'TO'
    if venue == 'L':
        # A USD line of an LSE-listed fund or GDR (CSPX-style UCITS
        # ETF): an LSE security on the UK cycle, not a fictional `.US`
        # one on the US T+1 calendar (audit A2-0081).
        return 'L'
    return ext
