"""What an asset code is to the books, one answer for every parser and the
crypto readers (PLAN step 14 moved them from lib/brokerages, whose
`_crypto_common` and `schema` re-export them): a fiat currency
(`FIAT_CURRENCIES`), a US-dollar stablecoin (`USD_STABLECOINS`, read at each
use), and the books' zero for a traded quantity (`QTY_ZERO`)."""
from collections.abc import Set as AbstractSet

from taxjson.lib.markets import fiat_currencies as _fiat


class _LiveStablecoins(AbstractSet):
    """The USD-pegged stablecoins, ONE list for every crypto consumer
    (the Coinbase and Kraken parsers, crypto-sends, fx-cash,
    fill-crypto): taxjson's shipped market data (lib/markets) with the
    project's ticker.map STABLE lines, read at each use — never a copy
    frozen at import (copies drifted apart: PYUSD/GUSD missing in some,
    audit S060-24 / re-audit A2-0589). In a Canada project they are
    US-dollar cash (tax-logic CA-CRYPTO-02); a US project books them as
    property valued at par. A membership test the BUILT-IN list
    answered yes is noted once per coin (the line to add if it is
    wrong)."""

    def __contains__(self, item) -> bool:
        if not isinstance(item, str) or not item:
            return False
        from taxjson.lib.markets import is_usd_stablecoin
        return is_usd_stablecoin(item.strip().upper(), note=True)

    def __iter__(self):
        from taxjson.lib.markets import usd_stablecoins
        return iter(sorted(usd_stablecoins()))

    def __len__(self) -> int:
        from taxjson.lib.markets import usd_stablecoins
        return len(usd_stablecoins())

    @classmethod
    def _from_iterable(cls, it):
        return frozenset(it)

    def __repr__(self) -> str:
        return f"USD_STABLECOINS({sorted(self)!r})"


USD_STABLECOINS = _LiveStablecoins()

# Fiat currencies an exchange row can be priced or quoted in, ONE list
# (lib/markets: taxjson/data/markets.toml) for the Coinbase and Kraken
# parsers, the IB currency tags and the price chain. Anything else is a
# crypto-asset: erring toward "crypto" books a visible phantom position,
# while a coin wrongly taken for cash would drop a disposition silently.
FIAT_CURRENCIES = _fiat()

# The books' zero for a traded quantity: a BUYSELL under it has no
# direction (the sign is the direction) and is refused. Parsers that can
# meet a sub-zero quantity (a Kraken dust-sweep leg of a few
# ten-billionths of a coin) leave the row out against this same value
# (tax-logic CA-CRYPTO-11 / US-CRYPTO-07).
QTY_ZERO = 1e-9
