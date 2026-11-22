"""The refusal of a ticker.map or .tt line that renames or joins a
derivative: an option contract or a future never becomes shares, nor
another contract, by a ticker change (_rename_derivative_check) or an
undated join (_join_derivative_check). Out of lib/tt_format, which the
ticker.map parser (lib/ticker_map_rules) read for these while
tt_format reaches the parser through lib/brokerages/base (step 8 of
the architecture plan: lib's import cycle)."""

from taxjson.lib.futures_prefixes import FUTURES_PREFIXES


def _derivative_kind(sym: str) -> str:
    """"an option contract" / "a future" for such a symbol, else ""."""
    from taxjson.lib.option_symbols import is_option_symbol
    if is_option_symbol(sym):
        return 'an option contract'
    if str(sym or '').startswith(FUTURES_PREFIXES):
        return 'a future'
    return ''


def _rename_derivative_check(old: str, new: str, date: str, where: str,
                             shown: str) -> None:
    """A RENAME between a share listing and a contract is refused (a
    contract never becomes shares by a ticker change: pre-release review
    H7), and so is one between two contracts: an option follows its
    underlying's ticker change (lib/renames), so the line to write is the
    stock's; a series with another expiry, strike or right is another
    contract, not a new name."""
    from taxjson.lib.option_symbols import parse_option_underlying
    ko, kn = _derivative_kind(old), _derivative_kind(new)
    if not ko and not kn:
        return
    if not ko or not kn:
        sym, kind = (old, ko) if ko else (new, kn)
        raise ValueError(
            f"{where}RENAME line renames {kind} ({sym}) "
            f"{'into' if ko else 'from'} a share listing: a ticker change "
            f"keeps the security, and a contract never becomes shares (or "
            f"shares a contract) by one — book the exercise, assignment or "
            f"sale as the broker's rows show it: {shown!r}")
    uo, un = parse_option_underlying(old), parse_option_underlying(new)
    if uo and un and old[len(uo.rsplit('.', 1)[0]):] == \
            new[len(un.rsplit('.', 1)[0]):] and uo != un:
        raise ValueError(
            f"{where}RENAME line renames an option contract: an option "
            f"follows its underlying's ticker change — declare the stock's "
            f"instead, `RENAME {date} {uo} {un}`: {shown!r}")
    raise ValueError(
        f"{where}RENAME line renames {ko} ({old}) into {kn} ({new}): another "
        f"expiry, strike or right is another contract, not a new name of "
        f"the same one — book the broker's rows as they are (an option "
        f"follows its underlying's ticker change: declare the stock's "
        f"RENAME): {shown!r}")


def _option_contract(sym: str):
    """(futures option?, market suffix or '', expiry, right, strike) of
    an OCC option symbol: what makes it one contract whatever the root
    is spelled (a class letter, an adjusted-series digit, a broker's own
    spelling of the underlying), or None for another symbol."""
    from taxjson.lib.option_symbols import (_OCC_OPTION_RE, parse_option_expiry,
                                            parse_option_right, parse_option_strike)
    m = _OCC_OPTION_RE.match(sym or '')
    if not m:
        return None
    return (m.group(1).startswith(FUTURES_PREFIXES), m.group(3) or '',
            parse_option_expiry(sym), parse_option_right(sym),
            parse_option_strike(sym))


def _join_derivative_check(keyword: str, frm: str, to: str, where: str,
                           shown: str) -> None:
    """An UNDATED ticker.map line (GLOBAL, TOBASE, a legacy JOURNAL, a
    RENAME without a date) makes FROM the same security as TO at every
    date. Refused (ValueError naming the line): an option contract or a
    future joined with a share listing (a contract is never the shares),
    an option joined with a future, and two option contracts that differ
    in expiry, right, strike or market (another contract; a US option and
    a Montreal option on one stock are different property). Allowed: a
    respelling of ONE contract — the same expiry, right, strike and
    market (a missing suffix matches any), only the root spelled
    otherwise (a class letter, an adjusted-series digit: `GLOBAL
    QZB.B250620C00010000.TO QZB250620C00010000.TO`) — and two futures
    (their spellings are not checked). (Second pre-release review, 6.)"""
    ko, kn = _derivative_kind(frm), _derivative_kind(to)
    if not ko and not kn:
        return
    if not ko or not kn:
        sym, kind, other = (frm, ko, to) if ko else (to, kn, frm)
        raise ValueError(
            f"{where}{keyword} joins {kind} ({sym}) with a share listing "
            f"({other}) at every date: a contract is never the same "
            f"security as shares — book an exercise, assignment or sale "
            f"as the broker's rows show it, and join the shares' symbols "
            f"(an option follows its underlying's line): {shown!r}")
    co, cn = _option_contract(frm), _option_contract(to)
    if co is None and cn is None:
        return                                  # two futures
    if co is None or cn is None or co[0] != cn[0]:
        raise ValueError(
            f"{where}{keyword} joins {ko} ({frm}) with {kn} ({to}): an "
            f"option and a future (or an option on a future and one on "
            f"shares) are different contracts: {shown!r}")
    if co[2:] != cn[2:] or (co[1] and cn[1] and co[1] != cn[1]):
        raise ValueError(
            f"{where}{keyword} joins two different option contracts "
            f"({frm}, {to}): another expiry, right, strike or market is "
            f"another contract — only a respelling of one contract (the "
            f"same expiry, right, strike and market) may be joined: "
            f"{shown!r}")
