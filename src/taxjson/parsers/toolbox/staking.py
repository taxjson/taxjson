"""A reward's books (PLAN step 14 moved them from the crypto parsers): a
staking or other reward is income at its fair value when it is credited, and
the coins are acquired at that value (tax-logic CA-INC-STAKING /
US-INC-STAKING, CA-CRYPTO-03 / US-CRYPTO-03); a reward the book counts as
cash (a fiat reward, or a US-dollar stablecoin in a cash book, CA-CRYPTO-02)
is the income alone, with no acquisition. The two crypto exports state a
reward differently and their rows differ, so each has its own legs:

- a ledger credit valued in US dollars (Kraken's, `ledger_reward_legs`): the
  DIVIDEND in US dollars (a fiat reward's in its own currency), its income
  less than the coins' cost when a commission was charged in another
  currency, a stablecoin in a property book at its 1.00 USD par; the
  acquisition carries no gross amount, and a reward with no USD value is
  left unpriced for taxjson-fill-crypto;
- a transaction row valued in its price currency (Coinbase's):
  `reward_value` chooses the value from its cells (the Subtotal, |Quantity|
  x Price, or the Total less its fee) and refuses cells that do not fit,
  and `valued_reward_legs` books both legs at that one value, the reward's
  evidence on the DIVIDEND.

The parsers read the cells and refuse a negative reward (a reversal); the
basis, the account and the parser's own tolerances are the caller's."""
from typing import Any, Callable, Dict, List, Tuple

from taxjson.model import money as M
from taxjson.parsers.toolbox.assets import FIAT_CURRENCIES as _FIAT_CURRENCIES
from taxjson.parsers.toolbox.assets import USD_STABLECOINS as _STABLECOINS

_CASH_STABLECOINS = _STABLECOINS


def ledger_reward_legs(asset, date, time, qty, txid='', usd_value=None,
                       gross_qty=None, fee_qty=0.0, income_usd=None,
                       fee_note='', *, stablecoins_as_cash,
                       account) -> List[Dict[str, Any]]:
    """[the DIVIDEND, the acquisition] of a reward a ledger credited (one
    leg when the reward is cash in this book). The basis is the caller's
    (the parse's): `stablecoins_as_cash`, the stablecoin model, and
    `asset`, the coin as the ledger names it (a stablecoin keeps its own
    name under both models); the rows' `account` its own."""
    # Carry the reward qty on the DIVIDEND record so fill_crypto_prices
    # computes income as qty*FMV. Without it the prices-filler defaults
    # qty to 1.0 and a $4k ETH reward of 0.001 ETH ends up as $4k income.
    # `usd_value` (the ledger's amountusd) prices BOTH legs here — the
    # income and the acquisition cost are the same FMV — and the fill
    # step leaves a priced row alone. `income_usd` overrides the
    # income side only (a commission charged in another currency
    # reduces the income, not the coins' cost — a stablecoin's too,
    # backlog TJ-0379); for a fiat reward it is in the reward's own
    # currency, the currency of its row.
    # `qty` is the NET coins credited (gross reward − Kraken's in-kind
    # commission); the commission is recorded in the description,
    # never in the USD `fee` field (it is coin-denominated and
    # already out of the quantity).
    desc = 'Staking Reward'
    if fee_qty and gross_qty:
        desc = (f"Staking Reward (net of Kraken commission: gross "
                f"{gross_qty:.10g} - fee {fee_qty:.10g} {asset})")
    elif fee_note:
        desc = f"Staking Reward (Kraken commission {fee_note})"
    div = {
        'action': 'DIVIDEND',
        'date': date, 'time': time, 'date_settle': date,
        'symbol': asset, 'quantity': qty, 'currency': 'USD',
        'net_amount': 0.0, 'gross_amount': 0.0,
        'type': 'dividend', 'account': account,
        'description': desc,
    }
    buy = {
        'action': 'BUYSELL',
        'date': date, 'time': time, 'date_settle': date,
        'symbol': asset, 'quantity': qty, 'currency': 'USD',
        'price': 0.0, 'net_amount': 0.0, 'fee': 0.0,
        'account': account,
        'description': desc,
    }
    if txid:
        # Suffix distinguishes the paired emissions so the sort-stage
        # dedup keeps both.
        div['id'] = f'{txid}-div'
        buy['id'] = f'{txid}-buy'
    if asset in _STABLECOINS and not stablecoins_as_cash:
        # Property mode (a US project): income at 1.00 USD a coin,
        # and the coins are acquired at that value. A commission
        # paid in another currency reduces the income only: the
        # coins credited in full cost their full par value (backlog
        # TJ-0379; it was taken off their cost too).
        inc = qty if income_usd is None else income_usd
        for leg, amt in ((div, inc), (buy, qty)):
            leg['price'] = M.units(amt / qty, 8) if qty else 1.0
            leg['net_amount'] = amt
            leg['gross_amount'] = amt
        return [div, buy]
    if ((stablecoins_as_cash and asset in _CASH_STABLECOINS)
            or asset in _FIAT_CURRENCIES):
        # Worth 1.0/unit by definition: income = qty, priced here.
        # NO acquisition leg: the trade books fold the stablecoins to
        # USD (the coin is later SPENT as a fiat quote, never sold
        # as an asset), so a USDC position would sit in the book
        # forever as a phantom long that nothing ever closes. Fiat
        # rewards (`dividend` on USD.HOLD) are cash income in their
        # own currency.
        inc = qty if income_usd is None else income_usd
        if asset in _FIAT_CURRENCIES:
            div['currency'] = asset
        div['price'] = 1.0
        div['net_amount'] = inc
        div['gross_amount'] = inc
        return [div]
    if usd_value is not None and qty > 1e-12:
        price = M.units(usd_value / qty, 8)
        for leg in (div, buy):
            leg['price'] = price
            leg['net_amount'] = M.units(usd_value, 8)
        div['gross_amount'] = M.units(usd_value, 8)
        if income_usd is not None:
            div['net_amount'] = M.units(income_usd, 8)
            div['gross_amount'] = M.units(income_usd, 8)
    return [div, buy]


def reward_value(where, qty, price, fee, *, subtotal, gross, fee_known,
                 check_qp: Callable[..., None], abs_tol, rel_tol,
                 total_rel_tol) -> Tuple[float, bool]:
    """(the reward's value, whether it is the net received) from a
    transaction row's cells: `subtotal` (0.0 when blank or absent),
    `gross` the |Total| (None when blank), `fee` with `fee_known` (its
    cell not blank), `qty` and `price`. The parser's own identity of a
    value against |qty| x price (`check_qp`, its tolerances and wording)
    and its band (`abs_tol`, `rel_tol`, and `total_rel_tol` for a Total
    against a Subtotal) refuse a cell that does not fit; `where` names
    the row in a refusal."""
    # The reward's value is what you RECEIVED: the Subtotal
    # (= quantity x price). Coinbase's Total adds back its
    # staking commission ("Fees and/or Spread", ~25-35% of
    # the gross reward) — coins you never received. Booking
    # the Total overstated staking income and the rewarded
    # coins' ACB by that commission. A blank Total with a
    # price is quantity x price (never $0 cost: fill-crypto
    # fills only a row with no price).
    net_known = True
    if subtotal:
        # A 10x Subtotal was 10x income and ACB with only
        # a warn-level schema note (re-audit A2-0250).
        check_qp(where, 'Subtotal', subtotal, qty, price)
        total = subtotal
    elif price and qty:
        total = price * abs(qty)
    elif gross is not None and fee_known:
        # Neither Subtotal nor Price: the net is the Total
        # less its commission. The gross Total used to be
        # booked as the income and the coins' cost, and
        # fill-crypto then derived the price from it
        # (backlog TJ-0510).
        if abs(fee) >= gross:
            raise ValueError(
                f"{where}: fee {M.shown(abs(fee), 2):,.2f} is not "
                f"less than the Total {M.shown(gross, 2):,.2f} and "
                f"the row has no Subtotal or Price — the "
                f"reward's value is unknown; refusing to "
                f"book it. Fill in its Subtotal from the "
                f"Coinbase statement.")
        total = gross - abs(fee)
    else:
        total = gross or 0.0
        net_known = False
    if (fee_known and gross is not None
            and (subtotal or (price and qty))):
        # The export's own identity: Total = the reward
        # received + the commission. A Total, Subtotal and
        # fee that disagree (gross 30 holding a fee of 40)
        # were booked and shown as commission evidence
        # (backlog TJ-0538).
        _tol = abs_tol + (
            total_rel_tol if subtotal else rel_tol) * total
        if abs(gross - (total + abs(fee))) > _tol:
            raise ValueError(
                f"{where}: Total {M.shown(gross, 2):,.2f} does not fit "
                f"{'Subtotal' if subtotal else '|Quantity| x Price'} "
                f"{M.shown(total, 2):,.2f} + fee {M.shown(abs(fee), 2):,.2f} = "
                f"{M.shown(total + abs(fee), 2):,.2f} — a wrong or "
                f"shifted column; refusing to book it. "
                f"Correct the row from the Coinbase "
                f"statement.")
    return total, net_known


def valued_reward_legs(symbol, qty, currency, price, total, date_str,
                       time_str, row_id, *, evidence: Dict[str, Any],
                       cash_coins, account) -> List[Dict[str, Any]]:
    """[the DIVIDEND, the acquisition] of a reward at its value `total` (in
    the row's `currency`, at its `price`), one leg when the reward is cash
    in this book: `cash_coins`, the stablecoins the caller's basis books as
    US-dollar cash; `evidence` the reward's record, on the DIVIDEND."""
    # Two-row pattern matches Kraken's staking handling and
    # the legacy cb_dividends.pl output: a DIVIDEND for the
    # income event (qty*FMV) and a zero-net BUYSELL that
    # acquires the tokens for the inventory pool. Carrying
    # qty on the DIVIDEND lets fill_crypto_prices compute
    # the right income value when price isn't already set.
    div = {
        'action': 'DIVIDEND',
        'date': date_str, 'time': time_str, 'date_settle': date_str,
        'symbol': symbol, 'quantity': abs(qty), 'currency': currency,
        'price': price, 'net_amount': total, 'gross_amount': total,
        'type': 'dividend', 'account': account,
        'description': 'Staking Reward',
    }
    # Preserve the export's original commission evidence for
    # the staking views. The monetary fee is a VALUE in the
    # export currency, not a separately debited cash quantity.
    # An absent/blank fee is unknown even when the books'
    # existing numeric default is zero. No accounting changes.
    div['staking_reward'] = evidence
    # Stake the reward into inventory at FMV (income = cost
    # basis). Without this the BUYSELL enters at $0 cost,
    # and the FMV gets taxed twice: once as staking income,
    # again as inflated capital gain when sold. Old
    # cb_dividends.pl carried the CSV's `total_price` here
    # for the same reason.
    buy = {
        'action': 'BUYSELL',
        'date': date_str, 'time': time_str, 'date_settle': date_str,
        'symbol': symbol, 'quantity': abs(qty), 'currency': currency,
        'price': price, 'net_amount': total,
        'gross_amount': total, 'fee': 0.0,
        'account': account,
        'description': 'Staking Reward',
    }
    if row_id:
        div['id'] = f'{row_id}-div'
        buy['id'] = f'{row_id}-buy'
    # A stablecoin reward is USD cash income (see
    # _STABLECOINS): no acquisition leg — the coin is
    # never sold as an asset, so a position would sit
    # in the book forever (same as Kraken).
    return [div, buy] if symbol not in cash_coins else [div]
