"""The cash a row moves through the foreign-cash ledger: its legs, each a
(currency, signed native flow) (_cash_legs), and each leg's own date
(_cash_leg_dates: cash in lieu paid days after the exchange counts when it
is paid). bin/taxjson_fx_cash.py builds the ledger from them;
lib/fx_cash_v2.py reads them too.

What says a row's amount is cash or not: its `cash_leg` when it has one
(a row read from a .tt line with the token; row_types CASH_LEG_KINDS),
else its type, its wording and its source, as the parsers leave them.
`cash_leg_of` is the kind that says the same of a row once those are
gone, which taxjson-convert-tt writes; `cash_meaning` is everything the
ledgers read of a row, which it compares before and after (backlog
TJ-5036).
"""
from typing import Any, Dict, List, Optional

from taxjson.lib.brokerages._crypto_common import (FIAT_CURRENCIES,
                                                   USD_STABLECOINS)
from taxjson.lib.row_types import (CASH_LEG_CASH, CASH_LEG_NONE, CASH_LEG_REWARD,
                                   IN_KIND_TYPES, OPENING_TYPE, TRANSFER_BOOK_VALUE_TYPE)
from taxjson.model.evidence import parse_commission_refunds, parse_corp_cash


# The BUYSELL types that move securities and no cash (_non_cash): an
# opening balance (a .tt OPENING line, `tjs opening`) records a
# position already held on the snapshot day, never a purchase (backlog
# TJ-0622).
_SECURITY_ONLY_TYPES = IN_KIND_TYPES + (TRANSFER_BOOK_VALUE_TYPE,
                                        OPENING_TYPE)

# Cash-moving actions and their direction. Sign-preserving amounts:
# a negative dividend (reversal) disposes, a negative fee (rebate)
# acquires — handled by flipping direction on negative flow.
# Units of a currency below which a reward brings none in (fx_cash_v2's).
_EPS = 1e-6
_INFLOW = ("DIVIDEND", "DIVIDEND_IN_LIEU", "INTEREST")
_OUTFLOW = ("TAX", "FEE")


# Fiat and USD-pegged coins: a reward or trade leg IN one of these is
# cash (the crypto parsers fold stablecoins to their fiat quote). The
# stablecoins come from the parsers' shared list: PYUSD/GUSD were left
# out here, so a Canadian PYUSD reward never entered the USD pool
# (re-audit A2-0589). A US book's base is USD, so a US stablecoin flow
# never reaches the ledger — but one priced in another currency (a
# Coinbase reward valued in EUR) did, until `stable_cash` said a US
# project's stablecoin is property (backlog TJ-0493).
def _cash_like(sym: str, stable_cash: bool = True) -> bool:
    """A fiat currency (the ONE fiat list, lib/markets) or, when
    `stable_cash` (Canada, CA-CRYPTO-02), a stablecoin (live: ticker.map
    STABLE lines apply). A US project's stablecoin is property
    (US-CRYPTO-02, US-FX-03)."""
    return sym in FIAT_CURRENCIES or (stable_cash
                                      and sym in USD_STABLECOINS)


def _non_cash(tx: Dict[str, Any], stable_cash: bool = True) -> bool:
    """A row whose consideration is PROPERTY, not currency (S003-05):
    it is valued in a currency for the gains engine, but no foreign
    cash changes hands, so it neither acquires nor disposes of any
    under ITA s.39(1.1). `stable_cash`: a USD stablecoin is US-dollar
    cash (Canada); False, a US project's, it is property (a reward in
    one moves no cash)."""
    # Securities moved in kind (lib/in_kind: into or out of a
    # registered plan at fair value; lib/transfer_in: arriving at the
    # broker's book value): a BUYSELL that values the shares, with no
    # cash leg — read as a trade it made up cash in the account or a
    # debt it never had (backlog TJ-0494).
    if (tx.get("type") or "") in _SECURITY_ONLY_TYPES:
        return True
    # The row says so itself (a .tt line's `cash_leg=`): its wording is
    # not asked.
    kind = str(tx.get("cash_leg") or "")
    if kind == CASH_LEG_NONE:
        return True
    if kind == CASH_LEG_CASH:
        return False
    if kind == CASH_LEG_REWARD:
        return not _reward_symbol_is_cash(tx, stable_cash)
    desc = str(tx.get("description") or "")
    low = desc.lower()
    # Crypto-for-crypto: Kraken trade/instant-trade legs, Coinbase
    # Convert legs. Both sides are priced in USD; neither moves USD.
    if "crypto-to-crypto" in low or low.startswith("convert ("):
        return True
    # A fee Kraken took in a coin (withdrawal, deposit, Hybrid Earn
    # withdrawal): the coins left, no dollars came in (A2-0576).
    if "(disposed at fmv)" in low:
        return True
    # In-kind staking reward in a coin (the income row and its
    # acquisition row). A reward paid in fiat or a stablecoin is cash.
    if low.startswith("staking reward"):
        return not _reward_symbol_is_cash(tx, stable_cash)
    # Corporate-action legs (share-for-share merger, spin-off ACB
    # allocation, taxable exchange at FMV): stock for stock. Their cash
    # (cash in lieu, boot) is the row's structured `corp_cash`, read by
    # _cash_legs (A2-1014); a book built before it had the field keeps
    # the standalone ': cash-in-lieu for' leg's wording. A cash takeover
    # is a sale the broker parser books, never a corp-action row.
    if tx.get("corp_event_id"):
        return ": cash-in-lieu for" not in low
    return False


def _reward_symbol_is_cash(tx: Dict[str, Any], stable_cash: bool) -> bool:
    """Whether the coin a reward row is paid in is cash in this book."""
    sym = str(tx.get("symbol") or "").upper().split(".")[0]
    return _cash_like(sym, stable_cash)


def _is_reward(tx: Dict[str, Any]) -> bool:
    """A reward's row (its income row or its acquisition): the row's
    `cash_leg` when it has one, else the parsers' wording."""
    kind = str(tx.get("cash_leg") or "")
    if kind:
        return kind == CASH_LEG_REWARD
    return str(tx.get("description") or "").lower().startswith(
        "staking reward")


def _reward_cash(row: Dict[str, Any], stable_cash: bool
                 ) -> Optional[tuple]:
    """(currency, units) of a reward paid in cash that the export prices
    in ANOTHER currency: a Coinbase "Rewards Income" of 5 USDC valued at
    6.85 CAD is booked as income in CAD (its DIVIDEND row's currency),
    while the 5 US dollars (a USD stablecoin is US-dollar cash,
    CA-CRYPTO-02) came into the account (backlog TJ-0038). None for
    any other row, or when the row's currency is the reward's own."""
    if row.get("action") != "DIVIDEND" or not _is_reward(row):
        return None
    sym = str(row.get("symbol") or "").upper().split(".")[0]
    cur = (sym if sym in FIAT_CURRENCIES else
           "USD" if stable_cash and sym in USD_STABLECOINS else "")
    units = abs(float(row.get("quantity") or 0.0))
    if not cur or cur == str(row.get("currency") or "").upper() \
            or units < _EPS:
        return None
    return cur, units


def cash_leg_of(tx: Dict[str, Any]) -> str:
    """The `cash_leg` (row_types CASH_LEG_KINDS) that says of this row
    what its type, wording and source say now, for a writer that keeps
    none of them (a .tt line); '' when the row's shape alone says it
    (an ordinary trade or income row, a corporate action's row with its
    `corp_cash`). A row that carries one keeps it."""
    kind = str(tx.get("cash_leg") or "")
    if kind:
        return kind
    low = str(tx.get("description") or "").lower()
    action = tx.get("action")
    if tx.get("corp_event_id"):
        # Stock for stock unless its cash is on the row (`corp_cash`,
        # the .tt `cash=`); a book built before that field words its
        # cash leg.
        if str(tx.get("corp_cash") or "").strip() \
                or ": cash-in-lieu for" not in low:
            return ""
        if action == "ADJUST" and not _broker_roc(tx):
            return ""
        return CASH_LEG_CASH
    if action == "ADJUST":
        return CASH_LEG_CASH if _broker_roc(tx) else ""
    if action not in ("BUYSELL", "ASSIGN", "DIVIDEND", "DIVIDEND_IN_LIEU",
                      "INTEREST", "TAX", "FEE"):
        return ""
    if _is_reward(tx):
        return CASH_LEG_REWARD
    if "crypto-to-crypto" in low or low.startswith("convert (") \
            or "(disposed at fmv)" in low:
        return CASH_LEG_NONE
    return ""


def cash_meaning(tx: Dict[str, Any]) -> tuple:
    """Everything the FX-on-cash ledgers read of one row, under both
    stablecoin models (cash, property): its dated legs
    (_cash_leg_dates), a reward's own-currency cash (_reward_cash) and
    its commission refunds (fx_cash_v2 `_refund_legs`). Two rows with
    the same meaning move the same currency on the same days."""
    out: List[Any] = []
    for stable in (True, False):
        out.append(tuple(_cash_leg_dates(tx, stable)))
        out.append(_reward_cash(tx, stable))
    out.append(tuple((r.date, r.amount) for r in
                     parse_commission_refunds(tx.get("commission_refunds"))))
    return tuple(out)


def same_cash_meaning(a: tuple, b: tuple, tol: float = 1e-4) -> bool:
    """Two `cash_meaning` results that are one, each amount within
    `tol` (a .tt line's total has five places)."""
    def flat(x: Any) -> List[Any]:
        if isinstance(x, (tuple, list)):
            return ["("] + [y for part in x for y in flat(part)] + [")"]
        return [x]
    fa, fb = flat(a), flat(b)
    if len(fa) != len(fb):
        return False
    for x, y in zip(fa, fb):
        if isinstance(x, float) and isinstance(y, float):
            if abs(x - y) > tol:
                return False
        elif x != y:
            return False
    return True


def _cash_legs(tx: Dict[str, Any], stable_cash: bool = True
               ) -> List[tuple]:
    """[(currency, signed native flow)] a row moves through the ledger.
    A corporate-action row with `corp_cash` ("<amount> <CUR>; ...")
    received exactly that cash, in that currency, whatever its
    description says (re-audit A2-1014). A futures settlement row that
    carries `_open_fee` (lib/futures.settle_futures_dicts, fee_basis)
    pays its own commissions on its own date, and a close's P/L leg
    leaves out every commission (its own and the openings' inside its
    basis, `_open_fee`; None on an opening part): an opening fee
    leaves the cash at the opening date's rate, not the close's
    (2026-10 audit #103); the total is unchanged. Any other row is
    `_flows` in its own currency (`stable_cash`: _non_cash)."""
    if (tx.get("type") or "") == "futures_settlement" and \
            "_open_fee" in tx and tx.get("action") == "BUYSELL":
        cur = str(tx.get("currency") or "").upper()
        own = float(tx.get("commission") or 0.0) + \
            float(tx.get("fee") or 0.0)
        legs = []
        if tx.get("_open_fee") is not None:
            # A close: its settled P/L before every commission.
            pl = float(tx.get("net_amount") or 0.0) + \
                float(tx["_open_fee"]) + own
            if abs(pl) > 1e-9:
                legs.append((cur, pl))
        if abs(own) > 1e-9:
            legs.append((cur, -own))
        return legs
    cc = str(tx.get("corp_cash") or "").strip()
    if tx.get("corp_event_id") and cc:
        return [(c, a) for c, a, _paid in _corp_cash_parts(cc)]
    flow = _flows(tx, stable_cash)
    if flow is None:
        return []
    return [(str(tx.get("currency") or "").upper(), flow)]


def _corp_cash_parts(corp_cash: str) -> List[tuple]:
    """[(currency, amount, paid)] of a `corp_cash` value ("<amount> <CUR>
    [ @<date>]; ..."; model/evidence `parse_corp_cash`, a part that does
    not read skipped); `paid` is '' for cash paid on the row's own
    date."""
    return [(c.currency, float(c.amount), c.paid) for c in parse_corp_cash(corp_cash)]


def _cash_leg_dates(tx: Dict[str, Any], stable_cash: bool = True
                    ) -> List[tuple]:
    """_cash_legs with each leg's own date: [(currency, flow, paid)],
    `paid` '' for the row's date. A corporate action's cash in lieu paid
    days after the exchange is folded into a row dated on the exchange;
    the ledger counts it when it came (backlog TJ-1063, TJ-1106)."""
    cc = str(tx.get("corp_cash") or "").strip()
    if tx.get("corp_event_id") and cc:
        return _corp_cash_parts(cc)
    return [(c, a, "") for c, a in _cash_legs(tx, stable_cash)]


def _flows(tx: Dict[str, Any], stable_cash: bool = True
           ) -> Optional[float]:
    """Signed cash flow of a transaction in its NATIVE currency:
    positive = cash received (acquires currency), negative = cash paid
    (disposes). None = not a cash event for this ledger."""
    action = tx.get("action")
    net = float(tx.get("net_amount") or 0.0)
    if _non_cash(tx, stable_cash):
        return None
    # ASSIGN: an assignment/exercise stock leg carrying the strike cash
    # (Webull books both legs ASSIGN; IB/RBC/Questrade book the stock
    # leg as BUYSELL) moves cash exactly like a trade (R1-147). The
    # option leg carries no cash (net 0) and is skipped below.
    if action in ("BUYSELL", "ASSIGN"):
        qty = float(tx.get("quantity") or 0.0)
        if net == 0:
            return None
        if (tx.get("type") or "") == "futures_settlement":
            # A futures fill moves only its settled P/L (lib/futures.py):
            # signed, + received / - paid. Its notional never changes
            # hands — counting it disposed of phantom USD (R1-204).
            return net
        # Buys consume cash, sells raise it; base books store net
        # magnitudes with the direction on quantity. A sale keeps its
        # sign: one whose commission exceeds its proceeds (net -0.25)
        # PAYS 0.25 (S033-09: abs() put phantom currency in the pool).
        # A buy never raises cash.
        return -abs(net) if qty > 0 else net
    if action in _INFLOW:
        # GROSS in, withholding out (the TAX row below). Every parser
        # books withholding as its own TAX row — RBC's "(Implied Tax)"
        # row, IB's withholding section — and merge2's
        # reconcile_dividend_tax rewrites a paired DIVIDEND's
        # net_amount to gross − tax. Taking NET here and the TAX row as
        # an outflow took the withholding out of the pool twice (a
        # phantom overdraft and a wrong gain on every withheld
        # dividend). Sign-preserving: a reversal's gross is negative.
        gross = float(tx.get("gross_amount") or 0.0)
        return gross if gross else net
    if action in _OUTFLOW:
        # Repo convention: TAX positive = withheld (cash out); FEE
        # positive = charged.
        return -net
    if action == "ADJUST" and _broker_roc(tx):
        # A return of capital a broker's export paid in cash (its
        # parser's tx_roc_adjust: net_amount = -cash received): the
        # cash came in, whatever it does to the security's cost. Every
        # ADJUST was skipped, and the IB cash events read no Dividends
        # row, so the receipt was in neither (backlog TJ-0846). A .tt
        # ADJUST (a T3's box 42 entered by hand: its cash came with
        # the distribution's dividend row), a row its parser marked
        # `no_cash` (RBC's year-end book-cost row, Value 0: backlog
        # TJ-1033) and the other adjustments move no cash.
        return -net
    return None


def _broker_roc(tx: Dict[str, Any]) -> bool:
    """A return-of-capital ADJUST a broker's export carries (type
    `roc`, from a file that is not a .tt, or a .tt line that says so:
    `cash_leg`) and paid in cash (not marked `no_cash`)."""
    import re
    if str(tx.get("type") or "").lower() != "roc" or tx.get("no_cash"):
        return False
    if tx.get("cash_leg"):
        # A .tt line's `cash_leg=cash`: the broker's row, written out.
        return tx["cash_leg"] == CASH_LEG_CASH
    src = re.sub(r"#\d+$", "", str(tx.get("source") or "")).lower()
    return bool(src) and not src.endswith(".tt")
