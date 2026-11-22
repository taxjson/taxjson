"""The engines' input rows: the one input contract, their ids, currencies and
dates, and the warnings on them.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib.stage_msg import emit_line
import functools
from typing import (
    TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, TypeAlias,
    Union,
)
from datetime import datetime

from taxjson.lib.corporate_timeline import account_tie
from decimal import Decimal

from taxjson.lib.row_types import OPENING_TYPE, TRANSFER_BOOK_VALUE_TYPE, TRANSFER_CUSTODY_TYPE
from taxjson.lib.tx_load import DATE_FIELDS, canonical_date

from taxjson.engine.assign import _MARKED_LEG_MAX_LAG_DAYS
from taxjson.engine.consts import _HALF_CENT

if TYPE_CHECKING:
    from taxjson.model.transaction import TaxTransaction

# A row's id as the model declares it (TaxTransaction.id): None only until
# its __post_init__ computes it, so a map keyed by row ids is keyed by this.
RowId: TypeAlias = Optional[str]
# The rule each decision record of a run names (PLAN step 11): {decided row
# id: {record id: rule id}}, a record keyed by its own row's id (a Canadian
# loss's DISALLOW is the sale's, an addition its ADJUST's `WASH_<loss>__<trg>`;
# a US loss's wash record is the sale's or the cover's, a replacement's the
# replacement row's; a gain record its row's), and four kept apart from the
# record their row also holds: a filing position's would-be denial
# (`override_record`, beside a US retest's denial of the same row), a
# replacement another account closed before the loss (`closed_record`,
# beside the units of the same purchase still held), a US retest's
# disallowance of a row that has its own (`retest_record`; a row whose only
# disallowance is a retest keeps it under its id) and an in-kind
# contribution's sale (`in_kind_record`, beside its superficial loss's
# denial when the plan is one s.40(2)(g)(iv) does not name). The engines return it
# as `rules` when it is not empty; lib/pipeline.run_gains pops it after the
# trace sink, so no gains document carries it.
RuleTable: TypeAlias = Dict[RowId, Dict[RowId, str]]


def override_record(row_id: RowId) -> str:
    """The rule table's key of a filing position's (ALLOWLOSS) record of
    the loss row `row_id`: what the denial would have been (RuleTable)."""
    return f"ALLOWLOSS_{row_id}"


def in_kind_record(row_id: RowId) -> str:
    """The rule table's key of an in-kind contribution's record of the sale
    row `row_id`: a sale at fair market value (RuleTable)."""
    return f"INKIND_{row_id}"


def retest_record(row_id: RowId) -> str:
    """The rule table's key of a US retest's disallowance of the sale or
    cover `row_id` (US-WASH-22) when the row also holds a disallowance of
    its own, a sale's or a cover's (RuleTable; backlog TJ-4412)."""
    return f"RETEST_{row_id}"


def closed_record(row_id: RowId) -> str:
    """The rule table's key of a replacement, the purchase or short sale
    `row_id`, that another account sold or covered before the loss
    (RuleTable)."""
    return f"CLOSED_{row_id}"


class AmbiguousTransferDateError(ValueError):
    """A row rewritten from a TRANSFER sits inside a superficial-loss /
    wash-sale trigger window. Its date is the broker ARRIVAL date —
    for a custody move that is not an acquisition date, and using it
    as one could wrongly deny (or wrongly allow) a return-relevant
    loss. The engine refuses to guess; the user declares what the row
    is. (Balance/still-held usage is unaffected: shares held are held
    regardless of how they arrived.)"""


@functools.lru_cache(maxsize=8192)
def _is_iso_date(value: str) -> bool:
    """`value` is already canonical_date's form: a real calendar date
    written YYYY-MM-DD (cached: a book repeats a few hundred dates)."""
    try:
        return canonical_date(value, '', '') == value
    except ValueError:
        return False


# The actions that move a position or its cost: each names its security,
# so an empty symbol is refused where money is computed (issue #16). The
# cash rows (FEE, INTEREST, TAX) may stand alone, and so may an income row
# the engines book by its own amount.
SYMBOL_REQUIRED_ACTIONS = ('BUYSELL', 'ASSIGN', 'SPLIT', 'TRANSFER',
                           'ADJUST', 'OPENING_BALANCE', 'DISALLOW')


def _describe_row(t: TaxTransaction) -> str:
    """The row as the user can find it: the loader's file and index when
    it came through coerce_transaction_row, else its own fields."""
    ctx: Optional[str] = getattr(t, '_row_ctx', None)
    if ctx:
        return ctx
    src = f"{t.source}: " if getattr(t, 'source', '') else ""
    return (f"{src}{t.action or '?'} row (id={t.id!r}, symbol="
            f"{t.symbol!r}, date={t.date!r})")


def require_computable_rows(*books: Optional[Iterable[TaxTransaction]]) -> None:
    """The one input contract of the gains computation (issues #14, #16).
    Every engine entry (CanadaTaxRules/USATaxRules.compute_gains) and the
    preparation in front of it (pipeline.prepare_books) refuse, in the
    main book and the context books alike:

    * a BUYSELL/ASSIGN row that came in WITHOUT its quantity or
      net_amount key (coerce_transaction_row marks it; an explicit 0 is
      a real amount and stays legal) — the engine would book it at 0;
    * an action the engines do not book (it was skipped in silence);
    * a row whose date is empty (missing and null are refused when the
      row is read);
    * a date field (DATE_FIELDS) that is not a string holding a real
      calendar date written YYYY-MM-DD (a date object too): the loader
      writes every date so (canonical_date), and
      the engines compare date strings, so '2025-2-11' sorts after
      '2025-11-01' and a row handed in directly lost its denial (TJ-2702);
    * an empty symbol on a row that moves a position or its cost
      (SYMBOL_REQUIRED_ACTIONS).

    Raises InputContentError (a ValueError) naming the row."""
    from taxjson.lib.brokerages.schema import KNOWN_ACTIONS
    from taxjson.lib.cli_diag import InputContentError
    for book in books:
        for t in book or ():
            miss = getattr(t, '_missing_trade_fields', None)
            if miss:
                ctx, fields = miss
                raise InputContentError(
                    f"{ctx}: required field(s) {', '.join(fields)} missing "
                    f"on a {t.action} row — the engine would book it at 0. "
                    f"Fix the input data.")
            if t.action not in KNOWN_ACTIONS:
                raise InputContentError(
                    f"{_describe_row(t)}: unsupported action "
                    f"{t.action!r} — the engines book only "
                    f"{', '.join(sorted(KNOWN_ACTIONS))}. Fix the input "
                    f"data.")
            if not str(t.date or '').strip():
                raise InputContentError(
                    f"{_describe_row(t)}: required field date is empty — "
                    f"fix the input data.")
            for _fld in DATE_FIELDS:
                _d = getattr(t, _fld, None)
                if _d and not (isinstance(_d, str) and _is_iso_date(_d)):
                    raise InputContentError(
                        f"{_describe_row(t)}: {_fld}={_d!r} is not a real "
                        f"calendar date written YYYY-MM-DD — fix the input "
                        f"data.")
            if t.action in SYMBOL_REQUIRED_ACTIONS \
                    and not str(t.symbol or '').strip():
                raise InputContentError(
                    f"{_describe_row(t)}: required field symbol is empty "
                    f"on a {t.action} row — fix the input data.")


def carry_row_marks(src: TaxTransaction, dst: TaxTransaction) -> None:
    """Copy the loader's marks (the missing-trade-field marker and the
    row's file/index) from `src` to its rebuilt copy `dst`, so a tool
    that rebuilds a row cannot launder a missing amount into a 0."""
    for k in ('_missing_trade_fields', '_row_ctx'):
        v = getattr(src, k, None)
        if v:
            setattr(dst, k, v)


def require_trade_fields(transactions: Iterable[TaxTransaction]) -> None:
    """require_computable_rows on one list of rows (the name the CLIs
    called before issue #14 moved the check to the engine boundary)."""
    require_computable_rows(transactions)


def convert_currency(amount: float, from_curr: str, to_curr: str, rates: Dict[Any, Decimal], default_rate: float) -> float:
    """Convert amount from from_curr to to_curr.

    rates may be keyed by (from_curr, to_curr) tuples (preferred), or by
    from_curr strings — in the string form the dict's implicit target must be
    to_curr or the result is nonsense, so callers should prefer tuple keys.
    """
    if from_curr == to_curr:
        return amount
    rate = rates.get((from_curr, to_curr))
    if rate is None:
        rate = rates.get(from_curr)
    if rate is None:
        rate = Decimal(str(default_rate))
    return float(Decimal(str(amount)) * rate)


def _by_account_name(*books: Optional[Iterable[TaxTransaction]]
                     ) -> Tuple[Optional[List[TaxTransaction]], ...]:
    """Each book with its accounts' rows in account-name order, one
    account's rows in their own order (a stable sort; None stays None).
    The engines' same-moment tie-break, and every first-wins step
    before it (one copy of a split two brokers dated apart, duplicate
    ids), then never depend on the order the accounts' books were
    merged in — taxjson.toml's (issue #31; CA-DATE-14 / US-DATE-13)."""
    return tuple(None if b is None else sorted(b, key=account_tie)
                 for b in books)


def _disambiguate_duplicate_ids(*books: Optional[Iterable[TaxTransaction]]) -> None:
    """Give a row whose content (and so whose id) repeats an earlier
    row's a distinct id, in place, with one NOTE. The engines link a
    loss to its denial, and a replacement lot to its basis bump, by
    tx.id: two byte-identical rows shared one id, so only the first
    took its denial or bump and the rest vanished (audit R1-169, US;
    S069-21, Canada). `tjs run` never gets here with duplicates
    (parsers tag repeated fills "[fill #N]" and merge2 --dedup drops
    same-id rows); hand-made JSON passed to taxjson-gains can."""
    seen: Dict[RowId, int] = {}
    renamed = 0
    for book in books:
        for t in book or []:
            n = seen.get(t.id, 0)
            seen[t.id] = n + 1
            if n:
                new_id = f"{t.id}~{n + 1}"
                while new_id in seen:
                    n += 1
                    new_id = f"{t.id}~{n + 1}"
                seen[new_id] = 1
                t.id = new_id
                renamed += 1
    if renamed:
        emit_line(f"NOTE: {renamed} row(s) repeat an earlier row exactly "
              f"(same date, time, symbol, quantity, price and amount); "
              f"each is booked as a separate trade. If they are "
              f"duplicates, drop them (taxjson-merge2 --dedup).")


def _warn_undrained_adjustments(pending: Dict[Tuple[str, str], Decimal], engine: str) -> None:
    """End-of-run drainage check for staged option-assignment premium.

    ASSIGN option legs park their premium in a pending-adjustments dict to
    be consumed by the matching stock leg's BUYSELL. When the stock leg is
    absent or its symbol doesn't match (missing statement rows, symbol
    drift), the premium used to be dropped SILENTLY — the option's economics
    simply vanished from the books. Say so, per symbol, with the amount."""
    undrained = {s: amt for s, amt in pending.items() if abs(amt) > _HALF_CENT}
    if not undrained:
        return
    def _kname(k: Union[Tuple[str, str], str]) -> str:
        return f"{k[1]} ({k[0]})" if isinstance(k, tuple) else str(k)
    detail = ", ".join(f"{_kname(s)}: {amt:+.2f}"
                       for s, amt in sorted(undrained.items(),
                                            key=lambda kv: str(kv[0])))
    emit_line(f"warning: {len(undrained)} unconsumed option-assignment "
        f"adjustment(s) at end of {engine} gains run — {detail}. An ASSIGN "
        f"option leg staged this premium, but no stock trade in its "
        f"account could be paired with it: none on that symbol in the "
        f"assignment's direction from 3 days before to "
        f"{_MARKED_LEG_MAX_LAG_DAYS} days after the option row (missing "
        f"rows, a symbol mismatch, or a leg dated outside that window); "
        f"that premium is NOT reflected in any gain. Check the "
        f"underlying's buy/sell rows around the assignment date.")


def _currency_mismatch_advice(tx: TaxTransaction, pool_cur: str) -> str:
    """The fix a currency-mismatch error names (audit A2-0055/0191/0204:
    it named an underscore tool a project user never runs)."""
    if tx.action in ('ADJUST', 'DISALLOW'):
        return (f"Enter the amount in the pool's currency ({pool_cur}) — a "
                f"slip's CAD figure on a {pool_cur} listing is converted at "
                f"the row's date — or convert the whole book first "
                f"(taxjson-convert-currency --to <base>). `tjs run` "
                f"does this itself.")
    return ("One pool holds one currency: convert the book first "
            "(taxjson-convert-currency --to <base>), or keep the listings "
            "apart (.TO vs .US). `tjs run` converts before the "
            "filing books.")


def _warn_ticker_reused_after_rename(txs: Sequence[TaxTransaction],
                                     date_of: Callable[[TaxTransaction], str], *,
                                     rule_text: str) -> None:
    """A symbol that TRADES after the date a SPLIT renamed it away
    (audit A2-0197). Renames are dated (owner decision): such a row is
    NOT the renamed security — it is its own identical-property class
    for the superficial-loss / wash-sale rule (SplitTimeline.class_at).
    Right when another company reuses the ticker; wrong when the broker
    still books the renamed shares under the old ticker — ticker.map's
    `RENAME OLD NEW <date> late=fold` folds those rows into NEW (and
    `late=separate` records the other case). `tjs renames` lists
    the rows and `run --strict` stops until each is declared. One line
    per (symbol, rename)."""
    renamed: Dict[str, Tuple[str, str]] = {}
    for t in txs:
        if t.action != 'SPLIT':
            continue
        new = (getattr(t, 'symbol_new', '') or '').strip()
        if new and new != t.symbol:
            d = str(date_of(t) or '')
            if t.symbol not in renamed or d < renamed[t.symbol][0]:
                renamed[t.symbol] = (d, new)
    if not renamed:
        return
    seen = set()
    for t in txs:
        if t.action not in ('BUYSELL', 'ASSIGN') or t.symbol not in renamed:
            continue
        d_r, new = renamed[t.symbol]
        d = str(date_of(t) or '')
        if d > d_r and t.symbol not in seen:
            seen.add(t.symbol)
            emit_line(f"warning: {t.symbol} trades on {d}, after its "
                  f"rename to {new} on {d_r}: renames are dated, so "
                  f"taxjson treats these rows as a DIFFERENT security "
                  f"from {new} for {rule_text}. If the broker still books "
                  f"the renamed shares under {t.symbol}, declare it in "
                  f"ticker.map (RENAME {t.symbol} {new} {d_r} late=fold); "
                  f"if another company now uses the ticker, `late=separate`"
                  f" (`tjs renames`).")

@functools.lru_cache(maxsize=8192)
def _iso_ordinal(s: str) -> int:
    """The day number of `datetime.strptime(s, '%Y-%m-%d')`, cached (the
    scans ask for the same few hundred dates again and again)."""
    return datetime.strptime(s, '%Y-%m-%d').toordinal()


def _verify_share_conservation(position_rows: Iterable[TaxTransaction],
                                actual_qty_by_symbol: Dict[str, float],
                                engine_label: str, *, zero_ratio_skips: bool) -> None:
    """Conservation post-condition: replay signed position quantities with
    plain arithmetic (+= qty; SPLIT scales; rename migrates) and compare
    against the engine's end inventory per symbol. The replay is
    deliberately SIMPLE — no chunking, no wash logic, no basis — so
    regressions in the complex pool/lot machinery (ordering, crossing-zero,
    OB scaling, rename merges, double-applied splits) surface as one
    mismatch warning here. It shares the upstream split dedup and sort
    order with the engine (a second independent copy of those would
    recreate the exact multi-copy drift this guards against); everything
    downstream of them is independent.

    `position_rows`: engine-ordered rows already filtered to what the
    engine lets mutate inventory (taxable scope; income/transfer rows
    removed). `zero_ratio_skips`: the US engine skips a ratio-0 SPLIT's
    SCALING but still applies its rename migration (2026-08 fix — the
    old full-skip forked the book); the Canada engine applies the
    ratio blindly — mirror each.

    Emits `warning:`-prefixed stderr lines (picked up by the .diag /
    DIAGNOSTICS banner); never raises."""
    expected: Dict[str, float] = {}
    redirect: Dict[str, str] = {}

    def rkey(sym: str) -> str:
        while sym in redirect:
            sym = redirect[sym]
        return sym

    for t in position_rows:
        if t.action == 'SPLIT':
            ratio = float(t.quantity or 0.0)
            k = rkey(t.symbol)
            if not (ratio == 0.0 and zero_ratio_skips):
                expected[k] = expected.get(k, 0.0) * ratio
            target = (t.symbol_new or '').strip()
            if target and target != t.symbol:
                tk = rkey(target)
                if tk != k:
                    expected[tk] = expected.get(tk, 0.0) + expected.pop(k, 0.0)
                    redirect[k] = tk
        elif t.action in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE',
                          'TRANSFER'):
            # (TRANSFER: own-account move legs and a native book's
            # listing journal legs only — the callers pass no other.)
            k = rkey(t.symbol)
            expected[k] = expected.get(k, 0.0) + t.quantity

    actual: Dict[str, float] = {}
    for sym, qty in actual_qty_by_symbol.items():
        k = rkey(sym)
        actual[k] = actual.get(k, 0.0) + qty

    for k in sorted(set(expected) | set(actual)):
        e, a = expected.get(k, 0.0), actual.get(k, 0.0)
        if abs(e - a) > 1e-3:
            emit_line(f"warning: conservation: {engine_label} share-count "
                f"mismatch for {k}: replaying the transactions gives "
                f"{e:.4f} but the engine's inventory holds {a:.4f} "
                f"(delta {a - e:+.4f}). The gains for this symbol are "
                f"NOT trustworthy — this indicates an engine ordering/"
                f"split/opening-balance bug, not bad input data.")


def not_a_purchase(tx: Union[Dict[str, Any], TaxTransaction]) -> bool:
    """Shares that are held but were not ACQUIRED on the row's date: an
    opening balance (CA-OPEN-01 / US-OPEN-01), a transfer-in booked at
    the broker's book value on its arrival (CA-ACB-TRANSFER-BV /
    US-BASIS-TRANSFER-BV) or a sheltered account's transfer kept as a
    custody move (CA-SL-16 / US-WASH-23). Never a superficial-loss /
    wash-sale replacement nor a recent buy; they count as held. Dict or
    TaxTransaction."""
    t = tx.get('type') if isinstance(tx, dict) else getattr(tx, 'type', '')
    a = tx.get('action') if isinstance(tx, dict) else getattr(tx, 'action', '')
    return a == 'BUYSELL' and (t or '') in (OPENING_TYPE,
                                             TRANSFER_BOOK_VALUE_TYPE,
                                             TRANSFER_CUSTODY_TYPE)
