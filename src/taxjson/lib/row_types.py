"""The book rows' kinds the engine gives meaning to: the type strings
(a stock dividend, an opening, a transfer at book value, the in-kind
moves, a lot move, a listing journal, a §355 spin-off, a netted
own-account move), the row
predicates on them, a row's broker open/close marker, the actions
that move no pool, and MATERIAL_COST (the cost that makes a position
under the quantity tolerance a holding). Names only: the parsers write
them, the engine and the views read them (step 8 of the architecture
plan: out of lib/core, which every parser imported for them,
lib/missing_history and lib/pipeline)."""

from __future__ import annotations

import re
from typing import Any, Tuple


# Actions that never touch a symbol's ACB / basis pool, so their currency
# is never checked against it (both engines' currency guards skip them:
# income rows, a TRANSFER kept as evidence, a SPLIT that carries no
# money). Every other action with a currency must match the pool's — the
# raw (native-currency) pass in `tjs run` tests the same set (audit
# A2-0440).
POOL_FREE_ACTIONS = ('DIVIDEND', 'DIVIDEND_IN_LIEU', 'TAX', 'INTEREST',
                     'FEE', 'TRANSFER', 'SPLIT')


# A position under the quantity tolerance that still carries this much
# cost (half a cent, a cent once rounded) is a holding, not rounding
# residue (tax-logic CA-ACB-01; docs/limits.md).
MATERIAL_COST = 0.005


# A stock dividend (new shares delivered in kind), as the parsers emit
# it: a BUYSELL of the new shares at $0 with this `type`. A NEUTRAL fact
# — the parsers do not decide its tax treatment; each engine does
# (partition INPUTS-01). Canada: an acquisition at $0 cost (the declared
# amount is income and cost, added by the user; tax-logic CA-STKDIV-01),
# which counts for s.54. US: not a purchase — the new shares join the
# lots held, spreading their basis (§307) with the purchase dates
# carried over (§1223(5)); not a §1091 replacement (US-STKDIV-01).
STOCK_DIVIDEND = 'stock_dividend'


# Corporate-action rows the US engine books per parent lot (types set by
# lib/corp_actions): a §355 spin-off's two rows (the spun-off shares'
# BUYSELL and the parent's ADJUST, joined by corp_event_id) and a §356
# boot exchange's two rows (the old shares' SELL and the new shares'
# BUYSELL). Neither acquisition is "by purchase" for §1091(a).
SPINOFF_355_TYPE = 'spinoff_355'
# (the engine's own name for it: engine/us/terms REORG_356_TYPE)
REORG_356_TYPE = 'reorg_356'


# An s.86.1 spin-off's fraction sale that replaces the brokerage
# parser's own pair for that cash in lieu (lib/corp_actions
# CIL_TAKEOVER_TYPE, its own name for it): taxjson-merge2 drops the pair
# and clears the mark, so it never reaches the engines; a book that still
# holds it has no .tt line (lib/tt_format refuses the row).
CIL_TAKEOVER_TYPE = 'cil_takeover'


# A custody move between two of your own TAXABLE accounts, as a pair of
# TRANSFER rows (out of one account, into the other; same symbol,
# moment, quantity and description) that `tjs run` adds to a US
# blended book: the US engine moves the sender's FIFO lots — basis and
# purchase dates — to the receiver, with no disposition (US-BASIS-05).
# Canada pools the ACB across the accounts (s.47): a book of several
# accounts moves nothing; a book of one account moves its units only
# (CA-ACB-OWN-MOVE, _own_move_view).
LOT_MOVE_TYPE = 'lot_move'


# A journal between two listings of one security inside one account (a
# TSX line moved to its US line: lib/listing_journals, backlog TJ-0361):
# two TRANSFER rows `tjs run` writes into the NATIVE-currency books
# only (work/<account>_raw*.json, behind the holdings export), the
# out-leg of the listing the units left and the in-leg of the one they
# reached, sharing a `journal_pair` id. The engines move the units with
# their cost from one listing's pool to the other's on the journal's
# date: never a disposition, never an acquisition. The in-leg's `price`
# restates the cost in its listing's currency when the source pool is
# in another (_journal_factor). The tax books never hold these rows:
# they join the listings, and the broker's legs cancel there.
LISTING_JOURNAL_TYPE = 'listing_journal'


# An opening balance (`tjs opening`, a .tt OPENING line): a BUYSELL
# that sets a position and its cost on a positions report's date but is
# NOT a purchase — never a superficial-loss / wash-sale replacement and
# never "acquired in the window" (CA-OPEN-01, US-OPEN-01). Its date is
# the snapshot day; `lot_date` the real acquisition (US holding period).
OPENING_TYPE = 'opening'


def is_opening_row(tx: Any) -> bool:
    """An opening-balance row (BUYSELL of type OPENING_TYPE), dict or
    TaxTransaction."""
    t = tx.get('type') if isinstance(tx, dict) else getattr(tx, 'type', '')
    a = tx.get('action') if isinstance(tx, dict) else getattr(tx, 'action', '')
    return a == 'BUYSELL' and (t or '') == OPENING_TYPE

# Shares that arrived by transfer from outside the books, booked at the
# broker's stated book value on their ARRIVAL date (lib/transfer_in,
# CA-ACB-TRANSFER-BV / US-BASIS-TRANSFER-BV). Held like any lot, but
# never a purchase for the superficial-loss / wash-sale window: the
# arrival is not when they were acquired.
TRANSFER_BOOK_VALUE_TYPE = 'transfer_book_value'
# A sheltered account's unmatched TRANSFER row kept in the loss-rule
# context as a custody move (lib/pipeline._handle_transfers, the default
# policy — CA-SL-16 / US-WASH-23): its shares count as held, but the row
# is never an acquisition (nor a disposition) for the superficial-loss /
# wash-sale window. `[settings] transfers_as_acquisitions = true` books
# such rows as 'transfer_rewrite' instead (CA-SL-17 / US-WASH-24).
TRANSFER_CUSTODY_TYPE = 'transfer_custody'
# (lib/pipeline writes the literal; engine/us/wash reads it)
TRANSFER_REWRITE_TYPE = 'transfer_rewrite'


# An in-kind move between a TAXABLE account and a REGISTERED account of
# the same taxpayer (lib/in_kind, booked by `tjs run` from the pair
# of transfer rows or a .tt INKIND line):
# * IN_KIND_CONTRIBUTION_TYPE: the taxable account's SALE at fair market
#   value when its shares go into an RRSP/RRIF/TFSA/FHSA/RDSP/LIRA... —
#   a gain is taxed, a loss is denied for good (s.40(2)(g)(iv), CA-
#   INKIND-03): never a superficial loss, never added to any ACB. The
#   same type marks the PLAN's acquisition in the loss-rule context: a
#   purchase for s.54 whatever the transfer policy (CA-INKIND-04).
# * IN_KIND_DISPOSITION_TYPE: the same sale into a plan s.40(2)(g)(iv)
#   does not name (an RESP, a PRPP): a loss is an ordinary loss.
# * IN_KIND_WITHDRAWAL_TYPE: the taxable account's PURCHASE at fair
#   market value of shares withdrawn in kind from a plan (CA-INKIND-05 /
#   US-INKIND-02) — an acquisition like any other.
IN_KIND_CONTRIBUTION_TYPE = 'in_kind_contribution'
IN_KIND_DISPOSITION_TYPE = 'in_kind_disposition'
IN_KIND_WITHDRAWAL_TYPE = 'in_kind_withdrawal'
IN_KIND_TYPES = (IN_KIND_CONTRIBUTION_TYPE, IN_KIND_DISPOSITION_TYPE,
                 IN_KIND_WITHDRAWAL_TYPE)


# Whether a row's amount moved as cash, where its shape does not say: the
# row's `cash_leg` (model/transaction), a .tt line's `cash_leg=` token.
# lib/cash_legs reads it; a row without one is read by its wording and
# its source, as the parsers leave them (backlog TJ-5036).
# * CASH_LEG_NONE: the amount is a value, the consideration property (a
#   crypto-for-crypto leg, a fee taken in a coin): no cash moved.
# * CASH_LEG_REWARD: a reward paid in the row's symbol, its income row
#   or its acquisition: cash only where the symbol is (a fiat currency,
#   or a US-dollar stablecoin in a book that counts one as cash).
# * CASH_LEG_CASH: the amount was paid in cash: a return of capital a
#   broker paid (an ADJUST of type roc), a corporate action's own cash
#   leg (a row with `corp_event_id` and no `corp_cash`).
CASH_LEG_NONE = 'none'
CASH_LEG_REWARD = 'reward'
CASH_LEG_CASH = 'cash'
CASH_LEG_KINDS = (CASH_LEG_NONE, CASH_LEG_REWARD, CASH_LEG_CASH)


def is_stock_dividend(tx: Any) -> bool:
    """A parser's stock-dividend row (a $0 BUYSELL of new shares)."""
    t = tx.get('type') if isinstance(tx, dict) else getattr(tx, 'type', '')
    a = tx.get('action') if isinstance(tx, dict) else getattr(tx, 'action', '')
    return (t or '') == STOCK_DIVIDEND and a == 'BUYSELL'


def open_close_codes(tx: Any) -> Tuple[str, ...]:
    """The broker's open/close marker on a row ('O', 'C' or both, in
    the broker's order; () when the export has none) — IB's Trades
    Code, carried as `open_close`."""
    raw = str(getattr(tx, 'open_close', '') or '')
    return tuple(c for c in re.split(r'[;,\s]+', raw.upper())
                 if c in ('O', 'C'))


# type of a netted own-account registered move kept in the wash context
# as a balance-only TRANSFER row (lib/pipeline._handle_transfers stamps
# the legs _net_cross_account_transfers nets; engine/ca/indexes
# _own_move_held and engine/ca/denial _sl_identical_units read it).
OWN_MOVE_TYPE = 'own_account_move'
