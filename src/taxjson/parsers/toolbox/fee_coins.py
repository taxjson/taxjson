"""A crypto ledger's fee taken in coins, sold (PLAN step 14 moved it from
lib/brokerages/kraken.py, Kraken's `_fee_coin_sale`): a fee the exchange
took in a coin left your ownership, a disposition at FMV (tax-logic
CA-CRYPTO-03 / US-CRYPTO-03). A function of the row's facts and of the
basis the caller passes in (`fee_coin_sale`), with the value under which
a coin quantity below the books' zero is left out (`_DUST_USD`,
CA-CRYPTO-11 / US-CRYPTO-07) and the wording its refusals and notes share
with the Kraken parser, which imports them from here (`_units`,
`_usd_text`, `_TT_REMOVE`)."""
from typing import Any, Dict, List

from taxjson.lib.stage_msg import emit_line
from taxjson.model import money as M
from taxjson.parsers.toolbox.assets import QTY_ZERO
from taxjson.parsers.toolbox.assets import USD_STABLECOINS as _STABLECOINS
from taxjson.parsers.toolbox.numbers import strict_money


# Appended to every row-level refusal that tells the user to use a .tt
# file: the refusal aborts the whole file, so entering the .tt alone
# never unblocks the run (audit S061-04).
_TT_REMOVE = (' and remove the row from the export (the file is refused until '
      'it is gone)')


def _units(q: float) -> str:
    """A coin quantity in plain decimals (0.0000000004, not 4e-10)."""
    return f"{q:.12f}".rstrip('0').rstrip('.') or '0'


# A coin leg, fee or reward under the books' zero (schema.QTY_ZERO) is
# left out only when its VALUE is negligible too: worth at most this many
# US dollars (its own amountusd, and for a trade leg the share of the
# other side it would take). Above it the row is refused, never dropped:
# a tiny quantity carrying real value is a disposal, a purchase or income
# the books would lose (tax-logic CA-CRYPTO-11 / US-CRYPTO-07; pre-release
# review H1).
_DUST_USD = 0.01


def _usd_text(v) -> str:
    return "no USD value in the export" if v is None else f"{M.shown(v, 2):.2f} USD"


def fee_coin_sale(row, ctx, fee, fee_ccy, type_raw, date, time, txid, *,
                  fee_sym, fiat, account, fee_usd=None) -> List[Dict[str, Any]]:
    """[the sale of the fee coins] when a ledger fee was taken IN A
    COIN, else []. A network/withdrawal fee Kraken took in a coin
    (0.05 ABC on a 4 ABC withdrawal; or, with `feecurrency`, in
    another coin — audit S061-16) left your ownership: a disposition
    at FMV (tax-logic CA-CRYPTO-03 / US-CRYPTO-03). The same for a
    fee in a coin on a fiat withdrawal (A2-1018) or on a reward
    (A2-0582): without this row the fee coins stayed in the book
    forever. Fiat fees, and stablecoin fees in a cash-mode book, are
    cash (see kraken.py's _normalize_asset) and need no row. Valued at 1.00 USD
    for a stablecoin in property mode, else at `fee_usd` / the
    ledger's `feeusd` (the exchange's own figure, S060-22), else
    price 0 for taxjson-fill-crypto.

    The basis is the caller's (the parse's): `fee_sym`, the fee's coin
    as its book names it (a stablecoin folded to USD in a cash-mode
    book), and `fiat`, the currencies that book counts as cash; the
    rows' `account` its own."""
    if not fee or fee_sym in fiat:
        return []
    _fusd = row.get('feeusd')
    if fee_sym in _STABLECOINS:
        _v = abs(fee)
    elif fee_usd is not None:
        _v = abs(fee_usd)
    else:
        _v = (abs(strict_money(_fusd, 'feeusd', ctx))
              if _fusd not in (None, '') else 0.0)
    _valued = (fee_sym in _STABLECOINS or fee_usd is not None
               or _fusd not in (None, ''))
    if abs(fee) < QTY_ZERO:
        if _v > _DUST_USD:
            # A tiny quantity worth real money: a sale the books
            # would lose — refused, never dropped (H1).
            raise ValueError(
                f"Kraken {ctx} ({date}): a {type_raw} fee of "
                f"{_units(abs(fee))} {fee_ccy} is under the books' zero "
                f"({QTY_ZERO:g} units) but its feeusd is "
                f"{_usd_text(_v)} — a sale of 0 units cannot be "
                f"booked, and leaving it out would lose a real "
                f"disposition (a fee that small is left out only when "
                f"worth at most {M.shown(_DUST_USD, 2):.2f} USD). This account's "
                f"files are not booked until it is fixed: correct the "
                f"row in the export, or enter the fee's sale via a .tt "
                f"file{_TT_REMOVE}.")
        # Under the books' zero and worth nothing: no row (a 0-unit
        # sale has no direction and the schema refuses it); the
        # coins stay as a residue (CA-CRYPTO-11).
        emit_line(f"note: Kraken {ctx}: a {type_raw} fee of "
                  f"{_units(abs(fee))} {fee_ccy} "
                  f"({_usd_text(_v if _valued else None)}) "
                  f"is under the books' zero ({QTY_ZERO:g} units) — not "
                  f"booked as a sale.")
        return []           # the row's other legs, or the caller's non-event
    fee_tx = {
        'action': 'BUYSELL',
        'date': date, 'time': time,
        'date_settle': date,
        'symbol': fee_sym,
        'quantity': -abs(fee),
        'currency': 'USD', 'price': 0.0,
        'net_amount': 0.0, 'gross_amount': 0.0,
        'fee': 0.0, 'account': account,
        'description': (f"Kraken {type_raw} fee paid in {fee_ccy} "
                        f"(disposed at FMV)"),
    }
    if _v > 0:
        fee_tx['price'] = M.units(_v / abs(fee), 8)
        fee_tx['net_amount'] = M.units(_v, 8)
        fee_tx['gross_amount'] = M.units(_v, 8)
    if txid:
        fee_tx['id'] = f'{txid}-fee'
    return [fee_tx]
