"""What a broker's description text says, read the same way by every
parser (step 5f of the engine refactor): a payment in lieu of a dividend
(`PIL_DESC_RE`: "CASH IN LIEU OF DIVIDEND", "PAYMENT IN LIEU OF
DIVIDEND", Questrade's "SUBST PAY ... IN LIEU OF DIVIDEND", the phrase
IB's parser reads too; re-audit A2-0098), and a dividend reinvestment's
price marker (`REINV_CUR_RE`, `REINV_CUR`: REINV@C$ = CAD, U$ = USD;
`REINV_PRICE_RE` the price after it) with the money identity it must
fit (`reinvest_identity_error`) and the price the row is booked at
(`reinvest_row_price`). RBC and Questrade
read these; Questrade used to import RBC's privates for them. A dividend
row's share count and per-share rate as its description states them
(`_parse_div_qty_rate`: "ON 500 SHS", "0.24 PER SHARE", IB's "Cash
Dividend CAD 0.97"), read by IB, Questrade and RBC, moved from
lib/brokerages/base.py in PLAN step 14. The labels a parser puts on a
dividend row (`ISSUER_ROC_KEY`, `PIL_LABEL_RETURN_OF_CAPITAL`) are here
too."""
import re
from typing import Optional

from taxjson.parsers.toolbox.money import money_close
from taxjson.parsers.toolbox.numbers import DESC_NUMBER_RE, desc_number
from taxjson.model import money as M

# "DIV", "DIV." or "DIVIDEND(S)" after the phrase, never "DIVESTED".
PIL_DESC_RE = re.compile(r'\bIN\s+LIEU\s+OF\s+(?:A\s+)?DIV(?:IDEND|\.|\b)', re.I)
# The neutral fact on a dividend row its broker labels a return of capital
# when the issuer is foreign (by its ISIN, not Canadian): whether it is a
# dividend or lowers the cost is the characterize step's
# (taxjson.characterize.foreign_roc), which takes the key off. A parse alone
# leaves it on the row.
ISSUER_ROC_KEY = "_issuer_roc"
# The income_label of a payment in lieu whose underlying distribution the
# broker designates a return of capital (IB "(Return of Capital)"): a
# neutral fact the parser records; the income rules
# (taxjson.characterize.income_dating `pil_is_dividend`) read it (TJ-0729;
# PLAN step 14 moved it from lib/income_dating.py).
PIL_LABEL_RETURN_OF_CAPITAL = "return_of_capital"
# The split-fill mark a description ends in (" [fill #2]"), as base.py's
# `disambiguate_split_fills` writes it: the one pattern its readers use.
FILL_MARK_RE = re.compile(r'\s*\[fill #\d+\]$')
# The REINV@ marker a reinvestment row carries, one grammar for its
# currency letters (C$ = CAD, U$ = USD, CAD$ too) and its price: RBC
# spaces the marker, Questrade spells three letters; both read both.
# A currency is letters followed by `$`; a price follows such a
# currency or none (letters without the `$` are no marker: the price
# would be read with no currency to check it against, TJ-2177).
_REINV_AT = r'\bREINV\s*@\s*'
_REINV_CUR = r'(?:[A-Z]{1,3})\$'
REINV_CUR_RE = re.compile(_REINV_AT + r'([A-Z]{1,3})\$', re.I)
REINV_CUR = {'C': 'CAD', 'U': 'USD', 'US': 'USD', 'CA': 'CAD',
             'CAD': 'CAD', 'USD': 'USD'}
# The price after the marker: 'REINV@C$1,234.56' is 1234.56 (audit  # pii-ok
# S062-20 / S064-21), read by desc_number.
REINV_PRICE_RE = re.compile(_REINV_AT + r'(?:' + _REINV_CUR + r')?\s*' + DESC_NUMBER_RE, re.I)


def reinvest_identity_error(qty: float, cash: float, price: Optional[float],
                            price_cur: str, row_cur: str) -> Optional[str]:
    """A dividend reinvestment buys |qty| units at the stated price for
    the cash: the money identity the trade path enforces (re-audit
    A2-0268 — a 10x Value booked as ACB with only a schema ATTENTION).
    Skipped when the price is in ANOTHER currency than the row (the
    REINV@C$ marker on a USD row, U$ on a CAD row: real exports carry
    those). None = fits (or cannot be judged)."""
    if not price or price <= 0 or not qty or price_cur != row_cur:
        return None
    expect = abs(qty) * price
    if money_close(cash, expect, abs_tol=0.05, rel_tol=0.05, scale=expect):
        return None
    return (f"the cash {M.shown(abs(cash), 2):,.2f} does not fit |Quantity| {abs(qty):g}"
            f" x the reinvestment price {price:g} = {M.shown(expect, 2):,.2f}")

def reinvest_row_price(qty: float, cash: float, price: Optional[float],
                       price_cur: str, row_cur: str) -> Optional[float]:
    """The per-unit price a reinvestment row is booked at, in the ROW's
    currency: the stated price when it is in that currency, else the
    cash per unit. A REINV@C$ price on a USD row (U$ on a CAD row) is
    another currency's number: booked as the row's price it made the
    row check (|qty| x price vs the cash) warn on every such DRIP, and
    RBC's fee back-computed from it was nonsense (v0.24.1 leftovers, 8).
    The cost is the cash either way; the description keeps the quote."""
    if price and price_cur != row_cur and qty:
        return M.units(abs(cash) / abs(qty), 8)
    return price


# Shared dividend-description parsers. Real broker dividend rows include
# the share count and/or per-share rate as plain English inside the
# Description column; extracting them lets us populate `quantity` and
# `price` on the emitted DIVIDEND record so it self-describes (qty held
# at record date × per-share rate ≈ amount). Falls back to None when
# the pattern doesn't match so the caller can default to zeros.
_DIV_QTY_ON_SHS_RE = re.compile(
    r'\bON\s+' + DESC_NUMBER_RE + r'\s+SH(?:S|RS|ARES)?\b',
    re.IGNORECASE,
)
_DIV_PER_SHARE_RE = re.compile(
    # "USD 0.24 per Share" / "$0.50 PER SHR" / "0.09 per share" — the
    # currency prefix is optional because we don't actually need it
    # here (the row already carries currency).
    # A thousands comma is read whole ('$1,250.00 PER SHARE' was 250)  # pii-ok
    # and a decimal comma leaves the rate unset (audit S055-23).
    r'(?:[A-Z]{3}\s+|\$)?(?<![\d.,])' + DESC_NUMBER_RE
    + r'\s*PER\s*SH(?:R|ARE)?\b',
    re.IGNORECASE,
)
_DIV_CASH_DIVIDEND_RE = re.compile(
    # IB also ships dividend rows whose rate has no "per Share" suffix:
    # "Cash Dividend CAD 0.97 (Ordinary Dividend)". The per-share figure
    # always sits right after "Cash Dividend <CCY>", so anchor on that
    # phrase — matching a bare "<CCY> <number>" anywhere would catch the
    # ISIN or other stray digits.
    r'\bCash\s+Dividend\s+(?:[A-Z]{3}\s+|\$)' + DESC_NUMBER_RE,
    re.IGNORECASE,
)


def _parse_div_qty_rate(description: str, amount: float):
    """Return `(quantity, price)` for a dividend row.

    quantity: shares held at the record date (from "ON N SHS" pattern).
    price:    per-share dividend rate (from "X per Share" pattern, or
              derived from amount/quantity when only the count appears).

    Either or both default to 0.0 when the description is silent, in
    which case the emitting record falls back to the historical
    "qty=0, price=0" shape and downstream consumers unchanged.
    """
    qty = 0.0
    rate = 0.0
    m_qty = _DIV_QTY_ON_SHS_RE.search(description or '')
    if m_qty:
        # A decimal comma ('ON 1,5 SHS') is not a share count: left
        # unset (informational), never read as 15 (audit S062-13).
        qty = desc_number(m_qty.group(1), strict=False) or 0.0
    m_rate = _DIV_PER_SHARE_RE.search(description or '')
    if m_rate:
        rate = desc_number(m_rate.group(1), strict=False) or 0.0
    if rate == 0.0:
        # IB "Cash Dividend CAD 0.97" form — a rate without "per Share".
        m_cash = _DIV_CASH_DIVIDEND_RE.search(description or '')
        if m_cash:
            rate = desc_number(m_cash.group(1), strict=False) or 0.0
    # Derive missing field from the other when we have one + amount.
    if qty > 0 and rate == 0 and amount:
        rate = M.units(amount / qty, 8)
        # The paid amount is rounded to CENTS, so back-computing
        # manufactures spurious precision: 7 sh paid $2.33 yields
        # 0.33285714 for a dividend actually declared at 0.333 — and
        # the SAME payment in another account, from a broker whose
        # statement states the rate, showed a clean 0.333. Snap to
        # the FEWEST decimals that still explain the paid amount
        # within the half-cent tolerance the quantity snap below
        # uses. A genuinely fine-grained rate (0.0375 on a big
        # position) survives because no shorter form reproduces the
        # cash.
        # Only snap when the shorter form is UNAMBIGUOUS: at small
        # share counts the half-cent cash tolerance admits several
        # candidates, and blindly taking round(quotient, d) picked a
        # NEIGHBOUR of the declared rate (3 sh of a 0.555 dividend
        # gave 0.557) — and could even replace an exact quotient with
        # a worse one (4 sh at 0.0375 -> 0.037). Requiring the
        # neighbours at that precision to fail keeps the raw quotient
        # whenever the data genuinely cannot resolve the rate. At small
        # share counts the shortest consistent form can still be
        # SHORTER than the declared rate (7 sh paying $0.26 fits both
        # 0.037 and a declared 0.0375) — nothing in the data
        # distinguishes them, and the shorter form at least claims no
        # precision the cash cannot back.
        tol = 0.005 + 1e-9
        for _places in range(0, 9):
            cand = M.units(rate, _places)
            if abs(amount - cand * qty) > tol:
                continue
            step = 10.0 ** -_places
            if any(abs(amount - (cand + d * step) * qty) <= tol
                   for d in (-1, 1)):
                continue                       # ambiguous at this width
            rate = cand
            break
    elif rate > 0 and qty == 0 and amount:
        qty = M.units(amount / rate, 8)
        # The statement's amount is rounded to CENTS, so the division
        # lands NEAR the true share count, not on it (23 sh x 0.417 =
        # 9.591 -> paid 9.59 -> derived 22.99760192). When the nearest
        # integer count explains the paid amount to within the
        # half-cent rounding IB applies, snap to it. A genuinely
        # fractional DRIP position differs by more than the tolerance
        # unless it's within half a cent of the whole-share payout —
        # in which case the integer is the better estimate anyway.
        nearest = round(qty)
        if nearest > 0 and abs(amount - nearest * rate) <= 0.005 + 1e-9:
            qty = float(nearest)
    return qty, rate
