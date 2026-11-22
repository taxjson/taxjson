"""What the fixed-point loop reads, keeps per iteration and leaves for the
report; a loss's and a holding's records.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib import loss_overrides as _LO
from dataclasses import dataclass
from dataclasses import field as _dc_field
from typing import TYPE_CHECKING, AbstractSet, Any, Callable, Dict, FrozenSet, List, Optional, Set, Tuple
from datetime import datetime

from taxjson.lib.corporate_timeline import SplitTimeline

from taxjson.lib.option_symbols import _FUTURES_PREFIX_RE, is_option_symbol, parse_option_right
from taxjson.model.transaction import TaxTransaction
from taxjson.engine.premium_ledger import _AssignPremiumLedger
from taxjson.engine.rows import not_a_purchase

from taxjson.engine.ca.indexes import WashPlace, _DatedRows, _ClassBalances, _WashAdjustIndex
from taxjson.engine.ca.router import _MarkedShortRouter

if TYPE_CHECKING:
    from decimal import Decimal

    from taxjson.engine.rows import RowId, RuleTable
    from taxjson.engine.ca.pool import PoolBook
    from taxjson.engine.ca.pools import _Pools
    from taxjson.engine.ca.run_helpers import _Renames
    from taxjson.lib.cost_timeline import Recorder


_SL_BOOKKEEPING_ACTIONS = ('DISALLOW', 'ADJUST', 'DIVIDEND', 'TAX', 'INTEREST',
                           'FEE')      # rows that are neither purchases nor sales


@dataclass(slots=True)
class _SLIndexes:
    """The denial pass's indexes over the book, built by the first pass's
    setup and kept across the fixed-point loop's iterations (their inputs
    are fixed before the loop): `trg`, each class's candidate replacements;
    `call`, its long-call purchases; `sym`, each symbol's rows; `book_pos`,
    each row's book position. One holder in place of four names handed in
    and out of the pass and its parts on every iteration (backlog TJ-1841)."""
    trg: Optional[Dict[str, "_DatedRows"]] = None
    call: Optional[Dict[str, "_DatedRows"]] = None
    sym: Optional[Dict[str, "_DatedRows"]] = None
    book_pos: Optional[Dict[int, int]] = None
    # A pool's rows in one loss's window with the walk's units after each
    # (`_sl_pool_rows`), by (window, pool, tolerance); whether a trigger's
    # position is sold out before day 30 (`_sl_sold_out`), by (window,
    # trigger); a sale's triggers in CA-SL-10's order (`_sl_sale_alloc`), by
    # (sale, its last losing fill): the quantities and the book rows' order
    # are the book's, the same on every pass (TJ-2759, TJ-2769).
    pool_rows: Optional[Dict[Tuple[Any, ...], Tuple[List[TaxTransaction], Optional[TaxTransaction]]]] = None
    sold_out: Optional[Dict[Tuple[Any, ...], bool]] = None
    ordered: Optional[Dict[Tuple[Any, ...], List[TaxTransaction]]] = None

    def build(self, *, all_txs: List[TaxTransaction], rows_of_cls: Dict[str, List[TaxTransaction]],
              get_sort_date: Callable[[TaxTransaction], str],
              short_routed: AbstractSet[RowId] = frozenset()) -> None:
        """`trg`, `sym` and `book_pos` from the book (the first pass).
        `short_routed`: the rows the walk routed to a short pool (the
        run's `_short_routed`, complete once the first walk is done)."""
        self.pool_rows, self.sold_out, self.ordered = {}, {}, {}
        # Each class's candidate replacements (purchases), dated
        # by the sort date the trigger scan tests. A purchase of an
        # option series routed to its written pool is a buy-back: it
        # closes the account's obligation and acquires nothing, so it
        # never replaces, in any account (CA-SL-07, CA-OPT-12; backlog
        # TJ-3227). A share short's cover is read as it was.
        trg: Dict[str, _DatedRows] = {}
        for _c, _rows in rows_of_cls.items():
            _cand = [t for t in _rows
                     if t.action in ('BUYSELL', 'ASSIGN')
                     and not not_a_purchase(t)
                     and not (t.id in short_routed and is_option_symbol(t.symbol))]
            if _cand:
                trg[_c] = _DatedRows(_cand, get_sort_date)
        self.trg = trg
        # Each symbol's rows, for the window listing.
        _sym_rows: Dict[str, List[TaxTransaction]] = {}
        for t in all_txs:
            if t.action not in _SL_BOOKKEEPING_ACTIONS:
                _sym_rows.setdefault(t.symbol, []).append(t)
        self.sym = {_s: _DatedRows(_r, get_sort_date)
                    for _s, _r in _sym_rows.items()}
        self.book_pos = {id(t): i for i, t in enumerate(all_txs)}

    def build_calls(self, *, all_txs: List[TaxTransaction],
                    get_sort_date: Callable[[TaxTransaction], str],
                    call_und: Callable[[str], Optional[str]],
                    split_timeline: SplitTimeline) -> None:
        """`call`: the long-call purchases on each class (by the class of
        the underlying on the trade date). Built by the first pass with a
        LONG share loss, the first that reads it, as the scan built it."""
        _calls_of: Dict[str, List[TaxTransaction]] = {}
        for t in all_txs:
            if (t.action != 'BUYSELL' or t.quantity <= 0
                    or not_a_purchase(t)
                    or parse_option_right(t.symbol) != 'C'):
                continue
            _und = call_und(t.symbol)
            if not _und or _FUTURES_PREFIX_RE.match(_und):
                continue
            _calls_of.setdefault(
                split_timeline.class_at(_und, t.date), []).append(t)
        self.call = {c: _DatedRows(r, get_sort_date)
                     for c, r in _calls_of.items()}


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class _CanadaRun:
    """What the Canadian engine's fixed-point loop reads but never rebinds
    (bound before the loop; the loop's per-iteration names stay parameters):
    the books and their id sets, the settings, the inputs the engine's module
    helpers read (`_ca_*(run, ...)`, step 4r; the mutable scratch `_ev_memo`
    and `_lot_seq` among them), and the result containers the pass and the walk fill in place
    (`final_virtual_txs`, `loss_to_trigger`, `loss_to_triggers_multi`,
    `permanent_by_loss`, `adjust_to_trigger`, `wash_place`, `wash_windows`, `rules`, and
    `_sl_idx`, filled by the first pass): the
    names are frozen, those containers' contents are not. Built once before
    the loop, with every field named (a missing one is a TypeError there);
    the pass, the walk, the solve (`_ca_solve`: `_move_legs`,
    `_short_routed`) and the finish (`alias_of`, `transactions`) read it
    through `run` (steps 4g and 4o of the engine refactor, backlog TJ-1843);
    `tests/test_engine_run.py` pins that the loop rebinds none of them and
    that each is read."""
    _assign_underlying: Callable[[TaxTransaction], Optional[str]]
    _book_ids: FrozenSet[int]
    _disp_last_seen: Dict[int, TaxTransaction]
    _ev_memo: Dict[Any, Any]
    _grant_mode: bool
    _lot_seq: List[int]
    _move_legs: List[TaxTransaction]
    _opt_lineage: Dict[str, Set[str]]
    _opt_prev: Dict[str, str]
    _ov_plan: _LO.Plan
    _rows_of_cls: Dict[str, List[TaxTransaction]]
    _short_routed: Set[RowId]
    _move_notes: Set[str]
    _sl_idx: _SLIndexes
    _split_syms: Set[str]
    adjust_to_trigger: Dict[str, RowId]
    affiliated_ids: Set[RowId]
    alias_of: Callable[[str], str]
    all_txs: List[TaxTransaction]
    cost_timeline: Optional[str]
    detect_wash_sales: bool
    final_virtual_txs: List[TaxTransaction]
    get_sort_date: Callable[[TaxTransaction], str]
    grant_on_trade: bool
    loss_to_trigger: Dict[RowId, RowId]
    loss_to_triggers_multi: Dict[RowId, List[RowId]]
    option_buyback_loss_superficial: bool
    option_grant_since: Optional[int]
    permanent_by_loss: Dict[RowId, Decimal]
    row_cls: Callable[[TaxTransaction], str]
    # The rule of each decision record (engine/rows `RuleTable`): the pass
    # notes its denials' as it makes them, the finish keeps the live ones.
    rules: RuleTable
    sheltered_ids: Set[RowId]
    split_timeline: SplitTimeline
    taxable_ids: Set[RowId]
    taxable_stock_symbols: Set[Tuple[str, str]]
    trace: bool
    transactions: List[TaxTransaction]
    wash_place: Dict[RowId, WashPlace]
    wash_windows: Dict[RowId, Dict[str, Any]]
    year_end: Optional[str]
    year_end_basis: Optional[str]


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class _CanadaIteration:
    """One iteration of the Canadian engine's fixed-point loop, as the loop
    binds it before the walk: this iteration's rows and their denials, the
    containers the walk fills (gains, losses, traces, snapshots), the
    timeline recorder and its helpers, the marked-short router and its
    legs, the per-iteration ids and the rows' positions. Built once per
    iteration right before the walk; the walk and the denial pass read it (step 4i of the engine refactor, backlog TJ-1848)."""
    _dis_by_id: Dict[RowId, List[TaxTransaction]]
    _jdone: Set[str]
    _jlegs: Dict[str, Tuple[TaxTransaction, TaxTransaction]]
    _moved_legs: Dict[RowId, TaxTransaction]
    _msr: _MarkedShortRouter
    _msr_legs: List[Tuple[Tuple[str, int], int, TaxTransaction]]
    _pos: Dict[int, int]
    _tl: Optional[Recorder]
    _tl_dates: List[Tuple[str, str]]
    _tl_shorts: Optional[Recorder]
    current_tx_list: List[TaxTransaction]
    global_pools: PoolBook
    iteration: int
    iteration_grant_nil_closes: List[Dict[str, Any]]
    iteration_losses: List[Dict[str, Any]]
    iteration_realized_gains: List[Dict[str, Any]]
    iteration_trace: List[str]
    pending_adjustments: _AssignPremiumLedger
    pool_snapshots_this_iter: Dict[RowId, Dict[str, Any]]
    symbol_acb_traces: Dict[str, List[str]]


@dataclass(slots=True, repr=False, eq=False)
class _CanadaBooks:
    """What the Canadian engine's fixed-point loop leaves for the report:
    the converged pass's gains, grant nil closes, wash sales and pools,
    the pools and the units short at the year end, the
    cost timeline, the solver's verdict, the override planner's
    would-be denials, and the last iteration's rows and premium ledger.
    Built empty before the loop; the loop's converged pass (or the last
    pass after a non-convergence, `keep`) fills it; the finish reads and
    reshapes it (step 4o of the engine refactor)."""
    final_realized_gains: List[Dict[str, Any]] = _dc_field(default_factory=list)
    final_grant_nil_closes: List[Dict[str, Any]] = _dc_field(default_factory=list)
    final_wash_sales: List[Dict[str, Any]] = _dc_field(default_factory=list)
    final_global_pools: _Pools = _dc_field(default_factory=dict)
    # The pools at the end of `year_end` (YYYY-12-31, the project year's
    # last day, on this engine's settlement-date order), the converged
    # pass's: `tjs edge-cases` reads the denied losses still held at the
    # year end from them, not from the pools after every later row
    # (backlog TJ-0282). None without a year_end.
    final_year_end_pools: Optional[_Pools] = None
    # The converged pass's cost timeline (lib/cost_timeline; backlog
    # TJ-0417): (recorder, each row's (walk date, basis date)). None
    # unless asked for.
    final_timeline: Optional[Tuple[Recorder, Recorder, List[Tuple[str, str]]]] = None
    # Each account's units short at the end and at year_end (_pool_rows).
    final_short_units: Dict[Tuple[str, str], float] = _dc_field(default_factory=dict)
    final_ye_short_units: Dict[Tuple[str, str], float] = _dc_field(default_factory=dict)
    solver_converged: bool = False
    solver_iterations_used: int = 0
    _ov_would: Dict[RowId, Dict[str, Any]] = _dc_field(default_factory=dict)
    # The last iteration's row list (the book's rows and the virtual rows
    # placed) and its assignment-premium ledger, read by the finish.
    rows: List[TaxTransaction] = _dc_field(default_factory=list)
    premium_ledger: Optional[_AssignPremiumLedger] = None

    def keep(self, *, it: _CanadaIteration, msr: _MarkedShortRouter, tl: Optional[Recorder],
             tl_shorts: Optional[Recorder], tl_dates: List[Tuple[str, str]],
             ye_pools: Optional[_Pools], ye_shorts: Dict[Tuple[str, str], float]) -> None:
        """The pass's results as the converged ones (or the last pass's after
        a non-convergence): its gains, grant nil closes and pools from the
        iteration, each account's units short from the router, the year-end
        pools and units, the timeline (the recorders and the dates, or None
        without one)."""
        self.final_realized_gains = it.iteration_realized_gains
        self.final_grant_nil_closes = it.iteration_grant_nil_closes
        self.final_global_pools = it.global_pools
        self.final_year_end_pools = ye_pools
        self.final_short_units = dict(msr.short)
        self.final_ye_short_units = ye_shorts
        if tl is not None:
            assert tl_shorts is not None, "set with tl (_ca_solve)"
            self.final_timeline = (tl, tl_shorts, tl_dates)
        else:
            self.final_timeline = None


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class _SLLoss:
    """One loss as the trigger scan establishes it for the denial pass's
    other parts: the loss and its row, its settlement date and class alias,
    the window's end, the purchases and calls dated inside the window, the
    sale's ledgers (step 4h of the engine refactor, backlog TJ-1870)."""
    loss: Dict[str, Any]
    tx: TaxTransaction
    loss_date: datetime
    loss_alias: str
    # Whether shares of a symbol booked on a date are identical property
    # to the loss's (`SplitTimeline.identical`; one answer per pair): the
    # class is the coarser grouping, and a rename target's own rows
    # before the split window of the rename are not the source's
    # (CA-ACB-RENAME, backlog TJ-2699).
    same: Callable[[str, Any], bool]
    end_window_date: str
    loss_sort: str          # the sort date as the unit conversions' reference
    # The loss row pre-exists its sort date when it settles later than it
    # trades: the ladder booked it in the pre-existing phase, before a
    # same-date SPLIT, so its units are pre-split and the reference side of
    # every conversion applies a split dated exactly loss_sort (round-four
    # finding 3).
    loss_pre: bool
    potential_triggers: List[TaxTransaction]
    call_triggers: List[TaxTransaction]
    _call_ids: Set[RowId]
    _call_syms: Set[str]
    _gid: int
    _grp_claim: Dict[Any, float]
    _grp_state: Dict[str, Any]
    _trg_used: Dict[RowId, float]


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class _SLHeld:
    """What the day-30 window walk finds for one loss, for the allocation
    and the trace: the class's balance view, what each holder still owns at
    the end of day 30 and in all (the unit helpers `_sl_row_loss_units`,
    `_sl_call_units`, `_sl_avail_native` and `_sl_gone_by_end` take the
    loss's objects)."""
    _cb: _ClassBalances
    _k_end: Optional[int]
    _unit: bool
    # The class's balance rows as (row, units in the loss's units) pairs,
    # the identical-property view of the loss (`_sl_identical_units`).
    rows: List[Tuple[TaxTransaction, float]]
    _held_h: Dict[Any, float]
    held_substituted: float


@dataclass(frozen=True, slots=True, repr=False, eq=False)
class _SLPass:
    """What the denial pass's setup binds for this pass and its four parts
    read: the setup's facts (the class roots, the rename chain, the running
    balances before each row), the kept indexes, this iteration's positions
    and denials, the processing positions, and the scratch the pass's
    helpers fill once (`_open_memo`, `_wpos_cell`, the class balances'
    cell and views). The helpers are module functions over `pas` and `run`
    (`_sl_holder`, `_sl_opening_qty`, `_sl_class_balances`, ...; step 4r,
    TJ-1964); the run's own state the parts read through `run` (step 4h of
    the engine refactor, backlog TJ-1843)."""
    _adj_idx: "_WashAdjustIndex"
    _bal_before: Dict[RowId, float]
    _cls_root: Dict[str, str]
    _dis_by_id: Dict[RowId, List[TaxTransaction]]
    _disp_last: Dict[int, TaxTransaction]
    _disp_of: Dict[int, int]
    _grp_units: Dict[int, float]
    _ledgers: Dict[int, Tuple[Dict[RowId, float], Dict[Any, float], Dict[str, Any]]]
    _open_memo: Dict[int, float]
    _ov_would: Dict[RowId, Dict[str, Any]]
    _pos: Dict[int, int]
    _renames: _Renames
    _sl_bal_cell: List[Dict[str, List[TaxTransaction]]]
    _sl_bal_sym: Dict[str, List[TaxTransaction]]
    _sl_book_pos: Dict[int, int]
    _sl_call: Optional[Dict[str, _DatedRows]]      # None until a pass has a LONG share loss
    _sl_sym: Dict[str, _DatedRows]
    _sl_trg: Dict[str, _DatedRows]
    _sl_views: Dict[str, _ClassBalances]
    _wpos_cell: List[Dict[RowId, int]]
    current_tx_list: List[TaxTransaction]
    pool_snapshots_this_iter: Dict[RowId, Dict[str, Any]]
