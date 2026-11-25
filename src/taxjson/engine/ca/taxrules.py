"""The Canadian engine's TaxRules: prepare, solve, finish.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib import loss_overrides as _LO
from taxjson.lib.stage_msg import emit_line
import contextlib
import io
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Set, Tuple, cast

from taxjson.rules.country import CANADA as _CANADA, check_engine_allowed as _check_engine_allowed
from taxjson.lib.corporate_timeline import SplitTimeline, event_sort_key, split_in_settle_lag
from decimal import Decimal

from taxjson.model import money as M
from taxjson.lib.option_symbols import is_option_symbol
from taxjson.model.transaction import TaxTransaction
from taxjson.engine.assign import _make_assign_underlying_resolver
from taxjson.engine.consts import _ZERO, _CENT, _MILLI, _NANO, _MINUS_HALF_CENT
from taxjson.engine.rows import (
    require_computable_rows, _by_account_name, _disambiguate_duplicate_ids,
    _warn_undrained_adjustments, _warn_ticker_reused_after_rename, _verify_share_conservation,
)
from taxjson.engine.splits import _fold_per_account_rename_ratios
from taxjson.engine.replacement import (
    detect_right_replacement_matches, detect_unresolved_option_replacement_matches,
    detect_opening_lot_matches,
)
from taxjson.engine.taxrules import TaxRules
from taxjson.engine.ca.pools import (
    pool_is_flat, _warn_stranded_basis, _account_premiums, _fold_short_futures_pools, _pool_rows,
    _with_shorts, _warn_waiting_additions,
)
from taxjson.engine.ca.run_helpers import _futures_names, _ca_split_targets, _ca_sort_date
from taxjson.engine.ca.indexes import WashPlace, _WashAdjustIndex
from taxjson.engine.ca.own_moves import _is_lot_move, _own_move_view
from taxjson.engine.ca.router import (
    MarkedPositions, _router_move_key, _place_own_move_legs, _split_crossing_marked_rows,
)
from taxjson.engine.ca.holders import _SLIndexes, _CanadaRun

from taxjson.engine.ca.walk import (
    STKDIV_DELIVERED, STKDIV_DELIVERY, STOCK_DIVIDENDS_AFTER_COVER,
    STOCK_DIVIDENDS_OWED, _conserved_rows, stock_dividend_after_cover_line,
    stock_dividend_cost_unbooked_line, stock_dividend_near_cover_line)
from taxjson.lib.stock_dividend_cover import no_share_rows
from taxjson.lib.row_types import is_stock_dividend
from taxjson.engine.ca.solve import _ca_solve
from taxjson.engine.ca.denial import _ca_live_rules

if TYPE_CHECKING:
    from taxjson.engine.rows import RowId, RuleTable
    from taxjson.engine.ca.holders import _CanadaBooks
    from taxjson.engine.ca.pool import AcbPool


class SplitStraddlesSettlementError(ValueError):
    """A RENAME-split dated strictly between a trade's execution and
    its settlement: re-denominating the in-flight quantity would also
    need re-symboling mid-trade, so the engine refuses and the user
    re-dates the row's settlement (or the split). PLAIN splits inside
    a settle lag no longer refuse — the executed quantity/price are
    re-denominated through the ratio (money untouched) and booked
    correctly against the post-split pool."""


def _stock_dividend_lines(run: _CanadaRun, transactions: List[TaxTransaction],
                          gone: Dict[RowId, Dict[str, Any]]) -> None:
    """Say the line of each stock dividend row booked as no shares (`gone`), now that the
    walk is done: whether its cover closed a short in these books (and so took its
    `delivered_cost`), whether the books pool several accounts, the cover's tax year
    (`stock_dividend_after_cover_line`)."""
    if not gone:
        return
    by_id = {t.id: t for t in transactions}
    pooled = len({t.account for t in transactions if t.account}) > 1
    closed = run._ev_memo.get(STKDIV_DELIVERED, ())
    for _id in sorted(gone, key=str):
        cover = by_id.get(gone[_id]['cover'])
        emit_line(stock_dividend_after_cover_line(
            gone[_id], _id in closed, pooled,
            str(run.get_sort_date(cover))[:4] if cover is not None else ''))


class CanadaTaxRules(TaxRules):
    country = _CANADA

    @M.engine
    def compute_gains(self, transactions: List[TaxTransaction], sheltered_transactions: Optional[List[TaxTransaction]] = None, affiliated_transactions: Optional[List[TaxTransaction]] = None, cross_asset: bool = False, trace: bool = False, detect_wash_sales: bool = True, option_premium_timing: str = 'close', option_grant_since: Optional[int] = None, option_buyback_loss_superficial: bool = False, option_grant_basis: str = 'settle', loss_overrides: Sequence[Dict[str, Any]] = (), year_end: Optional[str] = None, year_end_basis: Optional[str] = None, cost_timeline: Optional[str] = None, registered_book: bool = False) -> Dict[str, Any]:
        """
        Detects Superficial Losses (Wash Sales) based on CRA rules and calculates gains.
        Handles multi-account pooling and iterative adjustments.

        `cost_timeline` ("settle" or "trade", the date basis its rows
        are dated on): also return each pool's quantity and cost after
        every row that changed it (`cost_timeline`, lib/cost_timeline;
        backlog TJ-0417), the converged pass's.

        ITA 54 disallows a loss as "superficial" when an affiliated person
        (you, your spouse, a corporation you control, your RRSP/TFSA, etc.)
        acquired identical property in the ±30 day window and still holds
        some at the end of day 30. We split the inputs:

        - `transactions`: YOUR taxable trades (the gains/losses we compute).
        - `sheltered_transactions`: YOUR sheltered-account trades (RRSP,
          TFSA, etc.). Same taxpayer, but the basis adjustment goes onto
          property whose ACB doesn't matter for tax purposes.
        - `affiliated_transactions`: OTHER affiliated persons' trades (e.g.
          your spouse). Trigger the same superficial-loss treatment from
          your perspective; the deferred loss attaches to the affiliated
          person's substituted property per ITA 53(1)(f) — tracked by
          your spouse on THEIR return, not yours.

        Both `sheltered` and `affiliated` are pure additive context for
        wash detection; passing them only ever increases the set of
        candidate-replacement trades.
        """
        _kw: Dict[str, Any] = dict(
            affiliated_transactions=affiliated_transactions,
            option_buyback_loss_superficial=option_buyback_loss_superficial,
            option_grant_basis=option_grant_basis,
            option_grant_since=option_grant_since,
            option_premium_timing=option_premium_timing,
            sheltered_transactions=sheltered_transactions,
            year_end=year_end,
            year_end_basis=year_end_basis)
        transactions, _owed, _gone = self._stock_dividends_owed(transactions, _kw, registered_book)
        _run = self._prepare(
            cost_timeline=cost_timeline,
            detect_wash_sales=detect_wash_sales,
            loss_overrides=loss_overrides,
            trace=trace,
            transactions=transactions, **_kw)
        _run._ev_memo[STOCK_DIVIDENDS_OWED] = frozenset(_owed)
        for _id, _f in _gone.items():       # (a covered short's row: its cost joins its cover)
            _run._ev_memo.setdefault(STKDIV_DELIVERY, {}).setdefault(
                _f['cover'], []).append((_id, max(M.of(_f['cost']), M.of(0.0))))
        books = _ca_solve(run=_run)
        res = self._finish(books=books, run=_run)
        _stock_dividend_lines(_run, transactions, _gone)
        if _owed:       # (the gains document keeps it: the views read these rows as the engine did, TJ-5281)
            res[STOCK_DIVIDENDS_OWED] = _owed
        if _gone:       # (a key of the gains files: every reader of the rows leaves these out)
            res[STOCK_DIVIDENDS_AFTER_COVER] = sorted(_gone, key=str)
        return res

    def _stock_dividends_owed(self, transactions: List[TaxTransaction], kw: Dict[str, Any],
                              registered_book: bool = False) -> Tuple[List[TaxTransaction], List[RowId],
                                         Dict[RowId, Dict[str, Any]]]:
        """The book with each stock dividend received by an account that is short the
        security written as what it is there, the short growing by the shares at no proceeds
        (CA-STKDIV-02: the row's quantity negative), and those rows' ids. One reading for
        every pass: the walk, the superficial-loss pass (its day-30 balances and its purchases
        read the rows' quantities) and the conservation check; as shares received, a loss was
        denied against shares the books never held (backlog TJ-5006, TJ-5280).

        The account that received the row decides, by its own position, as its own run reads
        it: each account's rows are walked once first on their own, silently and with no loss
        rule (units do not depend on the denials), and a row of shares that meets a short pool
        there is the short's. So the pooled books of several accounts and each account's own
        books read a row alike: a holder's receipt stays one while another account's short
        leaves the pool net short (the pool alone made it the short's, and the holder's shares
        were gone from the pooled books).

        A row posted with no short open, where the account was short on the row's
        entitlement date and covered before it, is that short's obligation, which its cover
        did not include (backlog TJ-5282; lib/stock_dividend_cover decides it from the rows
        alone, as every reader of the rows does): no shares, so it is left out of the book
        for every pass, the first walks here included (a $0 acquisition, it was shares held
        and a purchase in a loss's 30 days), and returned with its facts (the third value: `compute_gains` names the rows
        in the result's `stock_dividends_after_cover`, hands a row's `delivered_cost` to
        its cover and says the line). A row with no entitlement date stays as booked;
        where the account covered a short in the days before, a line says the row may be
        its. A `delivered_cost` on any other stock dividend row is said as not booked."""
        owed: Set[RowId] = set()
        # (the one decision, from the rows alone: lib/stock_dividend_cover)
        # (never in a registered book, `registered_book`: such an account is never short)
        gone, near = no_share_rows(transactions, entitled_lots=False, positions=MarkedPositions,
                                   registered=registered_book)
        for t in transactions:      # (a cost is above 0: one below is said, never booked)
            if is_stock_dividend(t) and (t.delivered_cost or 0.0) < 0:
                gone.get(t.id, {}).update(cost=0.0)
                emit_line(stock_dividend_cost_unbooked_line(t))
        transactions = [t for t in transactions if t.id not in gone]
        for acct in sorted({t.account for t in transactions
                            if is_stock_dividend(t) and t.quantity > 0}):
            with contextlib.redirect_stderr(io.StringIO()):
                probe = self._prepare(
                    cost_timeline=None, detect_wash_sales=False, loss_overrides=(), trace=False,
                    transactions=[t for t in transactions if t.account == acct],
                    **dict(kw, sheltered_transactions=None, affiliated_transactions=None))
                found: Set[RowId] = set()
                probe._ev_memo[STOCK_DIVIDENDS_OWED] = found
                _ca_solve(run=probe)
            owed |= found
        for _id in sorted(near, key=str):
            emit_line(stock_dividend_near_cover_line(near[_id]))
        for t in transactions:
            if is_stock_dividend(t) and (t.delivered_cost or 0.0) > 0 and t.id not in gone:
                emit_line(stock_dividend_cost_unbooked_line(t))
        if not owed and not gone:
            return transactions, [], {}
        return ([replace(t, quantity=-t.quantity)
                 if t.id in owed and is_stock_dividend(t) and t.quantity > 0 else t
                 for t in transactions],
                sorted(owed, key=str), gone)

    def _prepare(self, *, affiliated_transactions: Optional[List[TaxTransaction]], cost_timeline: Optional[str], detect_wash_sales: bool, loss_overrides: Sequence[Dict[str, Any]], option_buyback_loss_superficial: bool, option_grant_basis: str, option_grant_since: Optional[int], option_premium_timing: str, sheltered_transactions: Optional[List[TaxTransaction]], trace: bool, transactions: List[TaxTransaction], year_end: Optional[str], year_end_basis: Optional[str]) -> _CanadaRun:
        """The Canadian engine's preamble: the input contract, the books named, deduped and
        indexed (`TaxRules`' shared stages), the option legs paired, the futures pools, the
        rename ratios folded, the helpers bound; returns the run (`_CanadaRun`, what the
        loop reads but never rebinds)."""
        _check_engine_allowed(self.country)  # test-only guard (rules/country)
        # The computation's input contract, main and context books
        # (issues #14, #16): a trade without its amount, an empty date or
        # symbol, an action the engine does not book.
        require_computable_rows(transactions, sheltered_transactions,
                                affiliated_transactions)
        transactions, sheltered_transactions, affiliated_transactions = \
            cast("Tuple[List[TaxTransaction], Optional[List[TaxTransaction]], Optional[List[TaxTransaction]]]", _by_account_name(transactions, sheltered_transactions,
                             affiliated_transactions))
        # A move between two of your own taxable accounts changes nothing
        # in Canada: the ACB is one pool across them (s.47). A book of
        # one account moves its units (CA-ACB-OWN-MOVE). Its legs still
        # move the holdings a merger's per-account ratios are weighted
        # by (backlog TJ-0280).
        _booked = _own_move_view(transactions)
        _move_legs = ([t for t in transactions if _is_lot_move(t)]
                      if len(_booked) < len(transactions) else [])
        transactions = _booked
        # The same holds for futures (issue #76): each account's books
        # were settled on their own, so a close of a contract held in
        # several taxable accounts is re-averaged over all of them here
        # (lib/futures.pool_across_accounts; tax-logic CA-FX-04).
        from taxjson.lib.futures import pool_across_accounts
        transactions, _fut_notes = pool_across_accounts(list(transactions))
        for _n in _fut_notes:
            emit_line(_n)
        _disambiguate_duplicate_ids(transactions, sheltered_transactions,
                                    affiliated_transactions)
        transactions, sheltered_transactions, affiliated_transactions = \
            _fold_per_account_rename_ratios(
                list(transactions), list(sheltered_transactions or []),
                list(affiliated_transactions or []), moves=_move_legs)
        transactions, sheltered_transactions, affiliated_transactions = \
            self._dedupe_splits(transactions, sheltered_transactions,
                                affiliated_transactions)

        all_txs = transactions + (sheltered_transactions or []) + (affiliated_transactions or [])
        sheltered_ids = {t.id for t in sheltered_transactions}
        affiliated_ids = {t.id for t in affiliated_transactions}
        taxable_ids = {t.id for t in transactions}

        get_sort_date = _ca_sort_date

        # The user's filing positions against the rule (.tt ALLOWLOSS,
        # lib/loss_overrides; CA-SL-18): each names one sale whose denial
        # is not applied. `_ov_would` holds, per named row, what the
        # solver's last pass would have denied (and to which
        # replacements) — recorded, never booked.
        def _ca_sort(x: TaxTransaction) -> Tuple[Any, ...]:
            return event_sort_key(x, profile='ca_main', date_of=get_sort_date)
        _ov_plan = _LO.plan(loss_overrides, sorted(transactions, key=_ca_sort))

        # A SPLIT inside a trade's settle lag — after the execution
        # MOMENT (date, clock time) and before the settle date: the
        # engine orders by settle date, so the pool splits before the
        # executed (pre-split-denominated) quantity is consumed. The
        # executed quantity/price are re-denominated into post-split
        # terms (qty x ratio, price / ratio; the MONEY — net_amount —
        # is untouched), which books the trade correctly against the
        # post-split pool. Splits ON the settle date are already
        # handled by the pre-existing phase ladder and excluded here.
        # The execution side compares clock time because IB posts
        # corporate actions in an evening batch (20:25) dated the
        # trade day: a sale executed that morning was pre-split, and
        # a date-only "strictly between" test left phantom
        # fractional shares after an 11-for-10 split. A split with no
        # meaningful time (00:00:01, parser default) still sorts
        # before every same-day execution — the ladder's convention.
        # A RENAME-split (symbol changes) straddling the lag would
        # also need re-symboling mid-flight — refuse loudly, as
        # before (rare squared).
        _lag_splits = [t for t in all_txs
                       if t.action == 'SPLIT' and t.date]
        if _lag_splits:
            _redenoms: Dict[int, float] = {}
            for _i, _t in enumerate(all_txs):
                if not (_t.action in ('BUYSELL', 'ASSIGN') and _t.date
                        and _t.date_settle
                        and _t.date < _t.date_settle):
                    continue
                _f = 1.0
                for _sp in _lag_splits:
                    # The ladder places a SPLIT at its SORT date
                    # (settle when set): the straddle test must use
                    # the same date, or a settle-desynced SPLIT row
                    # re-denominates the trade while the pool splits
                    # later — silent phantom shares (round-six
                    # adversarial audit finding 2).
                    _spd = _sp.date_settle or _sp.date
                    if not (split_in_settle_lag(
                                _t.date, _t.time, _t.date_settle,
                                _spd, _sp.time)
                            and _sp.symbol == _t.symbol):
                        continue
                    _new = getattr(_sp, 'symbol_new', '') or ''
                    if _new and _new != _sp.symbol:
                        raise SplitStraddlesSettlementError(
                            f"{_t.symbol}: the RENAME-split dated "
                            f"{_sp.date} {_sp.time or ''} ({_sp.symbol} "
                            f"-> {_new}) falls between the {_t.date} "
                            f"{_t.time or ''} execution and "
                            f"{_t.date_settle} "
                            f"settlement of a {_t.quantity:g}-share "
                            f"trade (account {_t.account}) — the "
                            f"trade would need re-symboling "
                            f"mid-flight. Set that row's date_settle "
                            f"equal to its trade date, or re-date "
                            f"the SPLIT off the lag.")
                    if float(_sp.quantity or 0):
                        _f *= float(_sp.quantity)
                if abs(_f - 1.0) > 1e-12:
                    _redenoms[_i] = _f
            if _redenoms:
                all_txs = list(all_txs)
                for _i, _f in _redenoms.items():
                    _t = all_txs[_i]
                    _d = _t.to_dict()
                    _d['quantity'] = float(_t.quantity) * _f
                    if float(_t.price or 0):
                        _d['price'] = float(_t.price) / _f
                    _r = TaxTransaction(**{**_d, 'id': _t.id})
                    all_txs[_i] = _r
                    emit_line(f"NOTE: {_t.symbol}: re-denominated a "
                          f"{_t.quantity:g}-share trade executed "
                          f"{_t.date} through the x{_f:g} split "
                          f"inside its settle lag (booked as "
                          f"{_d['quantity']:g} post-split shares; "
                          f"money unchanged).")
                # Rebuild the per-book lists from the same slices
                # (all_txs is transactions + sheltered + affiliated,
                # in order) so every downstream walk sees the
                # re-denominated rows.
                _n1 = len(transactions)
                transactions = all_txs[:_n1]
                _n2 = _n1 + len(sheltered_transactions or [])
                sheltered_transactions = all_txs[_n1:_n2]
                affiliated_transactions = all_txs[_n2:]

        # A trade that crosses between a marked short and the long pool is two
        # rows from here on (CA-DISP-04); a one-account book's own-move leg first
        # placed after the row that gives it its position (TJ-3242).
        _uncut = transactions
        transactions = _split_crossing_marked_rows(
            _place_own_move_legs(transactions, _ca_sort, get_sort_date, moves=_move_legs),
            _ca_sort, moves=_move_legs,
            move_key=lambda x: _router_move_key(x, get_sort_date))
        if transactions is not _uncut:
            all_txs = (transactions + (sheltered_transactions or [])
                       + (affiliated_transactions or []))
            taxable_ids = {t.id for t in transactions}

        # Each option ASSIGN's own stock leg is paired by identity in
        # the premium ledger (_pair_assign_legs: same account and
        # underlying, delivered quantity, strike, date window), TAXABLE
        # book only (re-audit: a sheltered/affiliated marked leg must
        # neither gate nor absorb the taxable premium). Keyed per
        # (ACCOUNT, symbol): in a combined multi-account book (the
        # blended pass) one account's leg never absorbs another
        # account's premium. A marked leg reserves only its own
        # option's premium (audit R1-33, A2-0052); an unrelated trade
        # sorted between the option and its leg cannot take it.
        # Option root -> the stock line its assignment delivers (the
        # root can differ from the ticker: SAMPLD for SAMPLD.B.TO, F:SAMPLX for
        # F:CLG6.US); see _make_assign_underlying_resolver.
        _assign_underlying = _make_assign_underlying_resolver(
            transactions, get_sort_date)

        # (ACCOUNT, underlying) pairs that actually trade as STOCK in
        # the taxable book. An option ASSIGN whose underlying is absent
        # for ITS OWN account is cash-settled (or missing its leg) —
        # its P&L must be realized on the option itself, not staged for
        # a stock leg that cannot exist. OPENING_BALANCE deliberately
        # does NOT count (re-audit: an OB-only underlying means the
        # assignment's stock leg is missing from the input — realizing
        # with the diagnosable note beats staging a premium nothing can
        # ever consume).
        taxable_stock_symbols = {
            (t.account, t.symbol) for t in transactions
            if not is_option_symbol(t.symbol)
            and t.action in ('BUYSELL', 'ASSIGN')}

        # One split/rename timeline per compute (SETTLE-basis dates, matching
        # this engine's window measurements). Virtual solver txs are never
        # SPLITs, so the timeline is stable across solver iterations — the
        # per-loss unit conversions below all query it.
        split_timeline = SplitTimeline.from_transactions(
            all_txs, date_of=get_sort_date)
        _warn_ticker_reused_after_rename(
            all_txs, lambda t: t.date,      # trade dates: a settle-lagged
            #                                 pre-rename sale is no reuse
            rule_text="the superficial-loss rule (CA-ACB-04)")

        # Ordering (phase ladder + tie-break priorities) is centralized in
        # lib/corporate_timeline.event_sort_key — profile 'ca_main' for the
        # solver pass, 'ca_balance' (no priority rung) for the running-
        # balance walks. The settle-lagged phase rationale and the
        # OB/SPLIT 00:00:00 tie rationale are documented there.

        # The containers the loop fills in place.
        final_virtual_txs: List[TaxTransaction] = []
        adjust_to_trigger: Dict[str, RowId] = {}  # adj_id -> trigger_lot.id (full)
        loss_to_trigger: Dict[RowId, RowId] = {}  # loss tx.id -> PRIMARY trigger id
        loss_to_triggers_multi: Dict[RowId, List[RowId]] = {}  # loss tx.id -> all allocated trigger ids
        permanent_by_loss: Dict[RowId, Decimal] = {}  # loss tx.id -> sheltered-allocated denial
        wash_place: Dict[RowId, WashPlace] = {}  # a denial's ADJUST id -> the row it is applied after
        wash_windows: Dict[RowId, Dict[str, Any]] = {}  # loss tx.id -> ±30d window listing
        rules: RuleTable = {}  # loss tx.id -> {record id: rule id} (engine/rows)

        # Symbol-alias map for SPLIT-renames. CRA s. 85.1(5) and
        # similar reorgs treat the pre- and post-rename security as
        # substantially identical for wash-sale purposes — so ABG.TO
        # and ABH.US must share a single time-series when computing
        # the 30-day affiliated balance, and a loss on ABG.TO must
        # consider ABH.US buys as potential wash triggers.
        #
        # Build equivalence classes via simple union-find on every
        # SPLIT with a non-empty symbol_new. The map sends every
        # symbol to its canonical class representative.
        # The union-find lives on the SplitTimeline (same union condition:
        # every SPLIT with a non-empty, non-self symbol_new; rename applies
        # regardless of the ratio's truthiness). This engine used to carry
        # its own copy — one more hand-maintained duplicate of the rename
        # chain, now deleted.
        alias_of = split_timeline.canonical
        # Identical-property matching is DATED (A2-0197, owner decision):
        # OLD before its rename date and NEW after it are one security;
        # an OLD row after the rename date is its own (another
        # company reusing the ticker, or a broker still booking the
        # renamed shares — ticker.map's `RENAME ... late=fold` folds
        # those into NEW before the engine runs). Same classes as
        # alias_of for every book with no such row. alias_of stays for
        # the warn-only replacement detectors. Within a class: `_sl_same_fn`.
        row_cls = split_timeline.class_of_row

        # Each class's rows in book order, grouped in one pass (one
        # walk of the book per class cost classes x rows: TJ-0105).
        _rows_of_cls: Dict[str, List[TaxTransaction]] = {}
        for t in all_txs:
            _rows_of_cls.setdefault(row_cls(t), []).append(t)
        
        # --- ITA s.49(1) option premium timing (see docs/design) -------
        # 'grant': a written option's premium is a capital gain on the
        # write date; a buy-back is a loss on its own date; expiry adds
        # nothing; a stock-settled assignment folds the premium into the
        # share leg and its grant record is not emitted (s.49(3)/(3.1)
        # deem the grant and exercise not to be dispositions; s.49(4)
        # only lets the grant year be reassessed to match). Contracts written before
        # `option_grant_since` keep close timing.
        #
        # Every short opening of a taxable option pool — pre-since
        # writes, the short leftover of a sell that crosses zero, and a
        # short OPENING_BALANCE included — is a LOT in the pool's
        # `grants` list, in write order. A close consumes the lots
        # strictly FIFO (the same order the pool walk sees the rows),
        # so an ASSIGN consumes exactly the units the old pre-scan
        # predicted. A recognised lot is carved out of the pool at its
        # own per-unit premium (so a buy-back of it is a loss of the
        # amount paid, whatever the other lots were written at); the
        # rest of the pool (close-timing lots, wash residue) keeps
        # average cost among itself. When an ASSIGN consumes a
        # recognised lot its grant record is retracted for those units
        # (s.49(3)/(3.1); s.49(4) reassesses the grant year) — the grant record is mutated in place within the
        # same pass, which is what the old pre-scan approximated from
        # outside the walk (it missed lots with no grant record: a
        # pre-since write or a cross-zero leftover consumed by the
        # ASSIGN double-counted the premium — 2026-09 engine audit).
        _grant_mode = (str(option_premium_timing or 'close').lower() == 'grant')
        _lot_seq = [0]

        # A book row's key, kept: the superficial-loss scans ask for it
        # once per loss and pass (TJ-0105). Book rows are never changed
        # here and all_txs keeps them alive (so no id is reused); the
        # solver's own rows (DISALLOW, ADJUST) are keyed afresh.
        _ev_memo: Dict[Any, Any] = {}
        _book_ids = frozenset(id(t) for t in all_txs)

        grant_on_trade = str(option_grant_basis).lower() == 'trade'

        # Each sale's pre-loss bump placement, kept across passes (see
        # _disp_last).
        _disp_last_seen: Dict[int, Any] = {}
        # The denial pass's indexes over the book, built by its first
        # pass and kept (the book never changes): see _SLIndexes.
        _sl_idx = _SLIndexes()
        # One pass over the SPLIT rows: the option lineages the day-30 call
        # walk sums whole (TJ-0716) and the split symbols. Rows are converted
        # by the underlying's split lineage, so the SPLIT row adds nothing.
        _opt_prev, _opt_lineage, _split_syms = _ca_split_targets(all_txs)
        # A share short the broker marks (RBC "SHORT." / "COVER SHORT.", IB
        # code O / C) is a position of its own beside the taxpayer's pooled
        # long shares, as a short future is (CA-DISP-04, backlog TJ-0721):
        # opening it never sells the shares another account holds, and a
        # purchase in another account never covers it. So are an option
        # series' written contracts, by the account's own position (CA-OPT-12,
        # TJ-2735). The main pass routes such rows to the symbol's short pool
        # (SHORT_POOL_SUFFIX) and records them here (_row_pool_key).
        _short_routed, _move_notes = cast("Set[RowId]", set()), cast("Set[str]", set())   # and the moves warned about (TJ-3243)

        _run = _CanadaRun(
            _assign_underlying=_assign_underlying,
            _book_ids=_book_ids,
            _disp_last_seen=_disp_last_seen,
            _ev_memo=_ev_memo,
            _grant_mode=_grant_mode,
            _lot_seq=_lot_seq,
            _move_legs=_move_legs,
            _opt_lineage=_opt_lineage,
            _opt_prev=_opt_prev,
            _ov_plan=_ov_plan,
            _rows_of_cls=_rows_of_cls,
            _short_routed=_short_routed,
            _move_notes=_move_notes,
            _sl_idx=_sl_idx,
            _split_syms=_split_syms,
            adjust_to_trigger=adjust_to_trigger,
            affiliated_ids=affiliated_ids,
            alias_of=alias_of,
            all_txs=all_txs,
            cost_timeline=cost_timeline,
            detect_wash_sales=detect_wash_sales,
            final_virtual_txs=final_virtual_txs,
            get_sort_date=get_sort_date,
            grant_on_trade=grant_on_trade,
            loss_to_trigger=loss_to_trigger,
            loss_to_triggers_multi=loss_to_triggers_multi,
            option_buyback_loss_superficial=option_buyback_loss_superficial,
            option_grant_since=option_grant_since,
            permanent_by_loss=permanent_by_loss,
            row_cls=row_cls,
            rules=rules,
            sheltered_ids=sheltered_ids,
            split_timeline=split_timeline,
            taxable_ids=taxable_ids,
            taxable_stock_symbols=taxable_stock_symbols,
            trace=trace,
            transactions=transactions,
            wash_place=wash_place,
            wash_windows=wash_windows,
            year_end=year_end,
            year_end_basis=year_end_basis)
        return _run

    def _finish(self, *, books: _CanadaBooks, run: _CanadaRun) -> Dict[str, Any]:
        """The Canadian engine's report from the converged books: the gains post-processed
        (the denied losses' s.53(1)(f) additions, the overrides, the T1135 fields), the
        summary, the option-replacement and undrained-adjustment warnings, the pools and
        the inventory rows, the cost timeline, over the run and the books; returns
        `compute_gains`'s dict."""
        _SHORT_SFX, _ = _futures_names()
        # Format results
        processed_gains = []
        by_ticker: Dict[str, Dict[str, Any]] = {}
        # Each denial's trigger row and ADJUSTs by id (first in list
        # order, as the walks of every row found them: TJ-0105).
        _book_row_of: Dict[Any, Any] = {}
        for t in run.all_txs:
            _book_row_of.setdefault(t.id, t)
        _adj_out = _WashAdjustIndex(run.final_virtual_txs)
        for g in books.final_realized_gains:
            # Signed cash-flow convention for SHORT positions: a sell-to-open
            # is a cash inflow (negative cost) and a buy-to-close is a cash
            # outflow (negative proceeds). This matches tt_gains.pl:401-405
            # so that consumers like taxjson_sum_gains.py can compute
            # `proceeds - cost = signed gain` without direction-aware logic,
            # and so taxjson_ccd_gains.py can filter shorts by `cost < 0`.
            entry_cost = g['cost']
            entry_proceeds = g['proceeds']
            if g['direction'] == 'SHORT':
                entry_cost = -entry_cost
                entry_proceeds = -entry_proceeds
            # Cross-engine schema: emit the same keys the US engine emits so
            # downstream consumers (sum-gains, explain, diff) can rely on
            # field presence without per-engine branching. Canada-specific
            # fields use None / 0.0 / [] for "concept doesn't apply here":
            #   - term: Canadian tax has no ST/LT distinction.
            #   - permanently_disallowed: the share of the denial whose
            #     replacement sits in a sheltered account (lost for good,
            #     CA-SL-09); the rest is deferred via the ACB bump.
            #   - wash_replacements: US-specific shape (basis_bump per rep);
            #     Canada uses wash_trigger + wash_window instead.
            tx_id_full = g['id' if 'id' in g else 'tx_id']
            trigger_id = run.loss_to_trigger.get(tx_id_full, '')
            gain_entry = {
                'date': g['date'],
                'date_settle': g.get('date_settle', g['date']),
                'symbol': g['symbol'], 'qty': g['qty'],
                'gain': g['taxable_gain'], 'raw_gain': g['gain'],
                'cost': entry_cost, 'proceeds': entry_proceeds,
                'currency': g.get('currency') or '',
                'commission': g.get('commission', _ZERO),
                'fee': g.get('fee', _ZERO),
                'disallowed_amount': g['disallowed'], 'is_wash_sale': g['disallowed'] > _MILLI,
                # Sheltered/affiliated-allocated portion of the denial —
                # lost for good (was hardcoded 0.0: FUZZ #D/#16 audit
                # hole; wash-sales/form-export described a deferral that
                # never existed).
                'permanently_disallowed': M.present(
                    run.permanent_by_loss.get(tx_id_full, _ZERO), 6)
                if g['disallowed'] > _MILLI else _ZERO,
                'replacement_lot_ids': run.loss_to_triggers_multi.get(
                    tx_id_full, [trigger_id] if trigger_id else []),
                'is_option': is_option_symbol(g['symbol'] or ''),
                'days_held': g['days_held'], 'account': g['account'], 'id': tx_id_full,
                'direction': g['direction'],
                'term': None,
                'wash_replacements': None,
                'tainted': g.get('tainted', False),
                # Grant-timing write records and s.40(3) deemed gains carry
                # a note naming the provision; consumers show it verbatim.
                'note': g.get('note', ''),
                'grant': bool(g.get('grant', False)),
                'deemed': bool(g.get('deemed', False)),
            }
            if g.get('own_move_from'):
                # Units moved in from another of your accounts, their
                # cost in the blended figures (backlog TJ-0279).
                gain_entry['own_move_from'] = list(g['own_move_from'])
            if g.get('unknown_cost_from'):
                # The OPENING cost=unknown rows the unknown units came
                # by: the run names the line from here (TJ-3209).
                gain_entry['unknown_cost_from'] = [
                    dict(_s) for _s in g['unknown_cost_from']]
            if g.get('in_kind'):
                # CA-INKIND-02/03: the sale of an in-kind contribution;
                # the s.40(2)(g)(iv) denial is its own figure, never the
                # superficial-loss one.
                gain_entry['in_kind'] = g['in_kind']
                gain_entry['denied_contribution'] = M.present(
                    M.of(g.get('denied_contribution') or _ZERO), 6)
            if g.get('deferred_loss_cut'):
                gain_entry['deferred_loss_cut'] = M.present(
                    M.of(g['deferred_loss_cut']), 6)
            if g.get('premium_after_cut'):
                gain_entry['premium_after_cut'] = M.present(
                    M.of(g['premium_after_cut']), 6)
            if g.get('deferred_loss_recovered'):
                # The earlier superficial losses (s.53(1)(f) additions)
                # in this disposition's cost — a short's: off its
                # proceeds — so `raw_gain` + this is its P/L before any
                # denial (CA-RPT-16).
                gain_entry['deferred_loss_recovered'] = M.present(
                    M.of(g['deferred_loss_recovered']), 6)
            if g.get('grant_closed') is not None:
                # A buy-back of grant-timed lots: {write year: {units,
                # premium}} of the lots it closed (see _short_lot_close).
                gain_entry['grant_closed'] = {
                    y: {'units': M.units(v['units'], 9),
                        'premium': M.present(v['premium'], 6)}
                    for y, v in g['grant_closed'].items()}
            if 'trace' in g:
                gain_entry['trace'] = g['trace']

            # Attach the wash-sale trigger info inline so the trace can
            # explain *why* the loss was disallowed alongside the disposition
            # itself, rather than punting to a separate section. Includes the
            # trigger buy and the ACB-adjust virtual transaction.
            if g['disallowed'] > _MILLI:
                trigger_id = run.loss_to_trigger.get(g['tx_id'])
                trigger_tx = None
                if trigger_id:
                    trigger_tx = _book_row_of.get(trigger_id)
                adj_vtx = next(iter(_adj_out.of(g['tx_id'])), None)
                wash_trigger: Dict[str, Any] = {
                    'is_full_disallowance': abs(g['taxable_gain']) < _CENT,
                    'loss_raw_gain': g['gain'],
                    'disallowed_amount': g['disallowed'],
                }
                if trigger_tx is not None:
                    wash_trigger['trigger_tx_id'] = trigger_id
                    wash_trigger['trigger_date'] = trigger_tx.date
                    wash_trigger['trigger_qty'] = trigger_tx.quantity
                    wash_trigger['trigger_price'] = trigger_tx.price
                    wash_trigger['trigger_account'] = trigger_tx.account
                    wash_trigger['trigger_sheltered'] = trigger_id in run.sheltered_ids
                    wash_trigger['trigger_affiliated'] = trigger_id in run.affiliated_ids
                if adj_vtx is not None:
                    wash_trigger['adjust_amount'] = adj_vtx.net_amount
                    wash_trigger['adjust_date'] = adj_vtx.date
                gain_entry['wash_trigger'] = wash_trigger
                if g['tx_id'] in run.wash_windows:
                    gain_entry['wash_window'] = run.wash_windows[g['tx_id']]

            processed_gains.append(gain_entry)
            # Skip tainted entries from by_ticker totals. They carry
            # fabricated numbers (cost basis = 0 against a missing-history
            # OPENING_BALANCE) and the CLI will route them out of
            # `transactions` into `manual_reporting_required` later.
            # Without this skip, any downstream consumer reading
            # by_ticker (rather than transactions) sees missing-history gains
            # mixed with real ones. The --year-rebuild path in
            # taxjson_gains.py has the same guard; this is the
            # primary-construction parallel.
            if gain_entry.get('tainted'):
                continue
            s = gain_entry['symbol']
            if s not in by_ticker:
                by_ticker[s] = {'total_cost': _ZERO, 'total_proceeds': _ZERO, 'total_gain': _ZERO, 'total_div': _ZERO, 'total_pil': _ZERO, 'trade_count': 0, 'hold_days': []}
            stats = by_ticker[s]
            stats['total_cost'] += gain_entry['cost']
            stats['total_proceeds'] += gain_entry['proceeds']
            stats['total_gain'] += gain_entry['gain']
            stats['trade_count'] += 1
            stats['hold_days'].append(gain_entry['days_held'])

        for tx in run.transactions:
            # PIL goes to a dedicated entry below so sum-gains can show it
            # in its own column. Keep it out of the eligible-dividend total
            # so the T5 / T3 dividend lines stay accurate (a payment in
            # lieu is other income, CA-INC-03).
            if tx.action == 'DIVIDEND_IN_LIEU' or tx.type == 'dividend_in_lieu':
                pil_amount = M.kept(tx.gross_amount if tx.gross_amount else tx.net_amount)
                pil_entry = {
                    'date': tx.date, 'date_settle': tx.date_settle or tx.date,
                    'time': tx.time, 'symbol': tx.symbol, 'qty': tx.quantity, 'currency': tx.currency,
                    'gain': _ZERO, 'cost': _ZERO, 'proceeds': _ZERO, 'pil': pil_amount, 'account': tx.account,
                    'id': tx.id, 'action': 'DIVIDEND_IN_LIEU'
                }
                processed_gains.append(pil_entry)
                s = pil_entry['symbol']
                if s not in by_ticker:
                    by_ticker[s] = {'total_cost': _ZERO, 'total_proceeds': _ZERO, 'total_gain': _ZERO, 'total_div': _ZERO, 'total_pil': _ZERO, 'trade_count': 0, 'hold_days': []}
                by_ticker[s]['total_pil'] += pil_entry['pil']
                continue

            if (tx.action == 'DIVIDEND' or tx.type == 'dividend') \
               and tx.action != 'DIVIDEND_IN_LIEU':
                # Report the gross dividend (pre-withholding) — that's what
                # T5/T3/1099-DIV expect on the income line. The withheld
                # foreign tax flows separately as TAX records and feeds the
                # foreign tax credit, not the dividend total.
                # Convention: parsers set gross_amount as the pre-withholding
                # dividend (e.g. RBC computes net/0.85 when description shows
                # NON-RES TAX WITHHELD); they leave it at the dataclass
                # default 0.0 when only net is known. We use gross if it's
                # truthy (positive — actual dividends are always > 0), else
                # net. Treating 0 and unset identically is intentional here:
                # a 0-gross "dividend" is contradictory data — no real
                # parser emits gross=0 + net>0 for a real dividend, and a
                # return-of-capital event should use action='ADJUST', not
                # DIVIDEND. If you ever need to distinguish "explicit zero"
                # from "missing", introduce a sentinel field on
                # TaxTransaction rather than changing this check.
                div_amount = M.kept(tx.gross_amount if tx.gross_amount else tx.net_amount)
                div_entry = {
                    'date': tx.date, 'date_settle': tx.date_settle or tx.date,
                    'time': tx.time, 'symbol': tx.symbol, 'qty': tx.quantity, 'currency': tx.currency,
                    'gain': _ZERO, 'cost': _ZERO, 'proceeds': _ZERO, 'dividend': div_amount, 'account': tx.account,
                    'id': tx.id, 'action': 'DIVIDEND'
                }
                processed_gains.append(div_entry)
                s = div_entry['symbol']
                if s not in by_ticker:
                    by_ticker[s] = {'total_cost': _ZERO, 'total_proceeds': _ZERO, 'total_gain': _ZERO, 'total_div': _ZERO, 'total_pil': _ZERO, 'trade_count': 0, 'hold_days': []}
                by_ticker[s]['total_div'] += div_entry['dividend']

        # Invariant: every dollar of disallowed loss reported in wash_sales should
        # also appear as the (taxable_gain - raw_gain) bump on the corresponding
        # gain entry. If they diverge, the engine has lost track of a wash sale.
        sum_taxable = sum(g['taxable_gain'] for g in books.final_realized_gains)
        sum_raw = sum(g['gain'] for g in books.final_realized_gains)
        sum_disallowed_on_gains = sum(g['disallowed'] for g in books.final_realized_gains)
        sum_disallowed_on_washes = sum(w['amount'] for w in books.final_wash_sales)
        # A loss denied for good by an in-kind contribution (s.40(2)(g)(iv))
        # is in taxable_gain and is no wash sale (TJ-1316).
        sum_denied_in_kind = sum(M.of(g.get('denied_contribution') or 0.0)
                                 for g in books.final_realized_gains)
        if abs((sum_taxable - sum_raw) - sum_disallowed_on_gains
               - sum_denied_in_kind) > _CENT:
            emit_line(f"warning: gain disallowance invariant broken — "
                f"taxable-raw={sum_taxable - sum_raw:.4f} vs gains-disallowed={sum_disallowed_on_gains:.4f}")
        if abs(sum_disallowed_on_gains - sum_disallowed_on_washes) > _CENT:
            emit_line(f"warning: wash-sale disallowance invariant broken — "
                f"gains-disallowed={sum_disallowed_on_gains:.4f} vs wash-sales={sum_disallowed_on_washes:.4f}")
        assert books.premium_ledger is not None, "the solve leaves its ledger (_ca_solve)"
        _warn_undrained_adjustments(books.premium_ledger.undrained(), self.country)

        # Options as replacement property are ENFORCED in the solver
        # above (a long call vs a share loss, s.54 para (i)). A warrant
        # or right, a call on an adjusted series or an option on the
        # same futures contract bought in the window is only NAMED
        # (warn-only, CA-SL-14/-15): what it converts into is not in
        # the books.
        _ca_losses = [{'symbol': g['symbol'], 'date': g.get('date_settle')
                       or g['date'], 'amount': g.get('raw_gain', g['gain']),
                       'id': g.get('id', ''),
                       'direction': g.get('direction', 'LONG')}
                      for g in processed_gains
                      if g.get('raw_gain', g['gain']) < _MINUS_HALF_CENT
                      and not g.get('tainted')]
        option_replacement_warnings: List[Dict[str, Any]] = \
            detect_right_replacement_matches(
                _ca_losses, run.all_txs, date_of=run.get_sort_date,
                canonical=run.alias_of,
                statute_label="ITA s.54 ('a right to acquire')")
        option_replacement_warnings += \
            detect_unresolved_option_replacement_matches(
                _ca_losses, run.all_txs, date_of=run.get_sort_date,
                canonical=run.alias_of,
                statute_label="ITA s.54 ('a right to acquire')")
        option_replacement_warnings += detect_opening_lot_matches(
            _ca_losses, run.all_txs, date_of=run.get_sort_date,
            canonical=run.alias_of, statute_label="ITA s.54")
        self._emit_replacement_warnings(option_replacement_warnings)

        # A short pool reports under its symbol, beside a long held in
        # another account.
        books.final_global_pools = _fold_short_futures_pools(books.final_global_pools)
        if books.final_year_end_pools is not None:
            books.final_year_end_pools = _fold_short_futures_pools(
                books.final_year_end_pools)
        # Conservation post-conditions (see the helpers' docstrings).
        _pool_qty: Dict[str, float] = {}
        _seen_pool_objs: Set[int] = set()
        for _s, _p in books.final_global_pools.items():
            if id(_p) in _seen_pool_objs:
                continue                # rename aliases share one object
            _seen_pool_objs.add(id(_p))
            # (a short beside a long nets here: this check counts units)
            _s = _s[:-len(_SHORT_SFX)] if _s.endswith(_SHORT_SFX) else _s
            _pool_qty[_s] = _pool_qty.get(_s, 0.0) + _p.qty
        _verify_share_conservation(_conserved_rows(run, books.rows), _pool_qty, self.country,
                                   zero_ratio_skips=False)  # CA applies a ratio-0 SPLIT blindly
        _warn_stranded_basis(books.final_global_pools)
        _warn_waiting_additions(books.final_global_pools)

        def _recognised_premium(p: AcbPool) -> Dict[str, Decimal]:
            if p.qty >= 0:
                return {}
            rp = M.present(sum((M.of(l['units']) * M.of(l['per_unit'])
                                for l in (p.grants or [])
                                if l.get('rec')), _ZERO), 4)
            # Signed: a write whose commission exceeds its premium (a
            # net debit) books a LOSS at the write; dropping it made
            # harvest charge it again in the buy-back (backlog TJ-1012).
            return {'recognised_premium': rp} if abs(rp) > _NANO else {}

        _ov_items = self._stamp_overrides(run._ov_plan, books._ov_would, processed_gains)
        _tl_basis = ''          # the timeline's date basis, read with `final_timeline` only
        if books.final_timeline is not None:
            assert run.cost_timeline is not None, "a timeline is kept only with cost_timeline set (_ca_solve)"
            _tl_basis = run.cost_timeline
        return {
            **({'loss_overrides': _ov_items} if _ov_items is not None else {}),
            **({'grant_nil_closes': books.final_grant_nil_closes}
               if books.final_grant_nil_closes else {}),
            'transactions': processed_gains,
            'by_ticker': by_ticker,
            'wash_sales': books.final_wash_sales,
            'option_replacement_warnings': option_replacement_warnings,
            'inventory': [
                {
                    'symbol': s,
                    # A short beside a long: that account's own
                    # (_pool_rows). `short_pool` tells it from a US lot.
                    **({'account': _ac, 'short_pool': True} if _ac else {}),
                    'qty': p.qty,
                    # Short pools hold the opening proceeds positive
                    # internally, but the repo-wide inventory convention
                    # (holdings export, `list`, USA engine) is NEGATIVE
                    # total_cost for shorts — proceeds credited, so
                    # cost_per_share = total_cost/qty is positive.
                    # FUZZ #21: this used to leak the internal positive
                    # sign, disagreeing with the USA engine and flipping
                    # COST vs COST/SH signs in `list`.
                    'total_cost': (M.of(p.total_cost)
                                   if p.qty >= 0
                                   else -M.of(p.total_cost)),
                    'currency': p.currency,
                    # Earliest open of the current run of holding this
                    # symbol. None for pools that drained and didn't
                    # reopen (those don't survive the qty filter
                    # anyway). Pre-data-window pools use the synthetic
                    # OPENING_BALANCE date — approximate but the best
                    # signal available.
                    'position_start_date': p.position_start_date,
                    # Most recent acquisition (any buy, incl. adds to an
                    # old position) — the date the superficial-loss /
                    # wash 30-day window measures from. None when the
                    # pool never saw an in-window buy (sentinel).
                    'last_acq_date': (None
                                      if p.last_acq_date
                                      in (None, '1970-01-01')
                                      else p.last_acq_date),
                    # Canada: the same acquisition on the s.54 window's
                    # own basis (settle dates, CA-SL-01) — what the
                    # harvest TX_ADD / SH_ADD columns measure from
                    # (A2-0958). last_acq_date above is the trade date.
                    'last_acq_settle': (None
                                        if p.last_acq_date
                                        in (None, '1970-01-01')
                                        else p.last_acq_settle
                                        or p.last_acq_date),
                    # Denied superficial losses still parked in this
                    # pool's ACB (deferred; recovered on a clean sale).
                    'deferred_wash': M.present(
                        M.of(p.deferred_wash), 4),
                    # Written-option lots whose premium was already
                    # recognised at the write (grant timing, s.49(1)):
                    # their buy-back is a loss of the whole amount paid,
                    # so a harvest view must not net this premium
                    # against the buy-back value (2026-09 audit R1-230).
                    **_recognised_premium(p),
                    **_account_premiums(p),
                    # Units whose cost these books do not have (a
                    # missing-history opening at an unknown cost; in one
                    # account's own view, units moved in from another of
                    # your accounts, CA-ACB-OWN-MOVE): total_cost leaves
                    # them out, and the holdings export says so (backlog
                    # TJ-0598).
                    **({'unknown_cost': True} if p.tainted else {}),
                }
                for s, p, _ac in _pool_rows(books.final_global_pools,
                                            books.final_short_units)
                if not pool_is_flat(s, p.qty, p.total_cost)
            ],
            # The pools at the end of year_end (see final_year_end_pools),
            # the inventory's sign convention for shorts.
            **({'inventory_year_end': [
                {'symbol': s,
                 **({'account': _ac, 'short_pool': True} if _ac else {}),
                 'qty': p.qty,
                 'total_cost': (M.of(p.total_cost) if p.qty >= 0
                                else -M.of(p.total_cost)),
                 'currency': p.currency,
                 'deferred_wash': M.present(
                     M.of(p.deferred_wash), 4),
                 **({'account_premiums': p.account_premiums}
                    if p.account_premiums and not _ac else {})}
                for s, p, _ac in _pool_rows(books.final_year_end_pools,
                                            books.final_ye_short_units)
                if not pool_is_flat(s, p.qty, p.total_cost)]}
               if books.final_year_end_pools is not None else {}),
            # The date the year end was read on, when not the walk's
            # settlement date (TJ-0343): edge-cases says which otherwise.
            **({'inventory_year_end_basis': 'trade'}
               if books.final_year_end_pools is not None
               and run.year_end_basis == 'trade' else {}),
            # Each pool after every row that changed it (backlog
            # TJ-0417): a day's pools are the rows before the first row
            # dated after it, as final_year_end_pools.
            **({'cost_timeline': _with_shorts(
                *[_r.finish(books.final_timeline[2], basis=_tl_basis,
                            stamp='max' if run.cost_timeline != 'trade'
                            else 'pool')
                  for _r in books.final_timeline[:2]])}
               if books.final_timeline is not None else {}),
            **({'rules': _live} if (_live := _ca_live_rules(    # (engine/rows RuleTable)
                run.rules, run.final_virtual_txs, books._ov_would,
                books.final_realized_gains)) else {}),
            'summary': {
                'total_gain': sum_taxable,
                'total_disallowed': sum_disallowed_on_washes,
                'wash_solver_converged': books.solver_converged,
                'wash_solver_iterations': books.solver_iterations_used,
            }
        }
