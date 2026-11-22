"""The quantity under which a Canada pool position is zero: a share
pool's millionth of a share, a coin pool's float noise. Out of lib/core,
so the cost timeline and the views read it without importing the
engine (step 8 of the architecture plan: lib's import cycle)."""

import functools


# Canada pool quantity tolerance (S069-13). A share pool treats less
# than a millionth of a share as zero: broker exports round share
# counts, and that absorbs the rounding and float noise. A coin is
# divisible far below that — a real residue of 9e-7 BTC is property
# with its own cost — so a crypto pool (a symbol with no market suffix)
# only absorbs float-arithmetic noise: a residue under 1e-11 of the
# larger of the position and the trade.
_SHARE_QTY_EPS = 1e-6
_COIN_QTY_REL_EPS = 1e-11
_COIN_QTY_ABS_EPS = 1e-15


@functools.lru_cache(maxsize=None)
def _is_coin_symbol(symbol: str) -> bool:
    from taxjson.lib.price_chain import is_crypto_symbol
    return is_crypto_symbol(symbol or '')


def pool_qty_eps(symbol: str, *scales: float) -> float:
    """The quantity under which a Canada pool position (or a leftover)
    is zero: 1e-6 for shares and options; for a coin, float noise
    relative to `scales` (the position and the trade quantities)."""
    if not _is_coin_symbol(symbol):
        return _SHARE_QTY_EPS
    scale = max((abs(float(x)) for x in scales), default=0.0)
    return max(_COIN_QTY_ABS_EPS, _COIN_QTY_REL_EPS * scale)
