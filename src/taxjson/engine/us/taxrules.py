"""The US engine's TaxRules: its three stages, prepare, walk and finish.

Moved from lib/core.py (PLAN step 10).
"""

from __future__ import annotations

from taxjson.lib import loss_overrides as _LO
from taxjson.lib.stage_msg import emit_line
import contextlib
import copy
import functools
import inspect
import io
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from taxjson.rules.country import USA as _USA, check_engine_allowed as _check_engine_allowed
from taxjson.lib.corporate_timeline import SplitTimeline, event_sort_key, renames_at_open

from taxjson.model import money as M
from taxjson.lib.option_symbols import exercise_target, is_option_symbol, parse_option_underlying
from taxjson.lib.row_types import LOT_MOVE_TYPE, SPINOFF_355_TYPE
from taxjson.model.transaction import TaxTransaction
from taxjson.engine.assign import (
    _pair_assign_legs, _place_assign_options, _make_assign_underlying_resolver,
)
from taxjson.engine.consts import _ZERO, _CENT, _MINUS_HALF_CENT
from taxjson.engine.journal import _is_listing_journal, _journal_legs
from taxjson.engine.premium_ledger import _AssignPremiumLedger
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
from taxjson.engine.us.lot_store import _us_ikey
from taxjson.engine.us.run import _RepIndex, _USRun
from taxjson.engine.us.terms import _per_account_rename_ratios, _US_EPSILON
from taxjson.engine.us.option_scan import detect_option_replacement_matches
from taxjson.engine.us.wash import (
    _us_split_covers, _us_get_sort_date, _us_non_capital, _us_dust_noise, _us_rep_key, _us_nkey,
    _us_taxable_rep_of, _us_say_retests,
)
from taxjson.engine.us.lots import _us_row_after, _us_tl_state

from taxjson.engine.us.prepare import (
    _us_open_qty, _us_lot_rows, _us_inventory_rows, _us_scan_events, _us_symbol_currencies,
    _us_disposition_groups, _us_paid_in_lieu, _us_plan_payments, _us_dividend_entries,
)
from taxjson.engine.us.walk import _us_walk_rows

if TYPE_CHECKING:
    from decimal import Decimal

    from taxjson.engine.rows import RowId, RuleTable
    from taxjson.engine.us.lot_store import _USInventory, _USLotsNow
    from taxjson.engine.us.trades import _GainRow


class USATaxRules(TaxRules):
    """US capital-gains engine: FIFO matching for long AND short positions
    with IRC §1091 wash-sale handling on both sides.

    Long positions:
    - FIFO sell-to-close matching, gain = proceeds - basis.
    - §1091: disallowed loss adds to basis of replacement BUY (deferred).
    - §1223(3): replacement inherits loss lot's holding-period start.
    - Rev. Rul. 2008-5: replacement in a sheltered account → *permanent*
      disallowance with no basis transfer.

    Short positions:
    - Sell-to-open creates a short lot (qty owed, proceeds received).
    - Buy-to-close pops short lots FIFO. Gain = opening_proceeds - closing_cost.
    - Holding period: stand-alone shorts are SHORT_TERM (the holding
      period of borrowed property is conventionally zero — see §1222).
    - §1091 extends to short sales (Reg §1.1091-1): a loss on closing a
      short followed by a substantially-identical sell-to-open within
      ±30 days disallows the loss and REDUCES the replacement short's
      effective opening proceeds (the short-side analog of basis bump).
    - Sheltered replacement short → permanent disallowance.

    Position flips:
    - A BUY exceeding short inventory closes shorts FIFO; leftover qty
      opens a new long lot.
    - A SELL exceeding long inventory closes longs FIFO; leftover qty
      opens a new short lot.

    Out of scope (v1) — to be added when a use case exists:
    - §1233(b)(1) anti-conversion: substantially identical property
      held NOT more than 1 year at the short sale (or acquired while the
      short is open) → gain on closing the short = SHORT_TERM.
    - §1233(b)(2) that long's holding period restarts when the short
      closes (or the long is sold).
    - §1233(d): substantially identical property held MORE than 1 year
      at the short sale → a loss on closing the short = LONG_TERM.
      (Each needs "substantially identical" reasoning beyond a simple
      symbol match.)
    - §1259 constructive sale of appreciated long when hedged by short.
    - §1091(e)(1): a SALE of substantially identical stock within ±30
      days of a short-cover loss does not disallow it (only a re-short
      registers as a short-side replacement) — tax-logic US-WASH-19.
    - Section 1256 year-end mark-to-market for futures and broad-based
      index options (SPX, NDX, futures); their P/L is booked FIFO here,
      and the estimate and carryover split the year's net 60/40
      (tax-logic US-FUT-03).
    """

    country = _USA

    WASH_WINDOW_DAYS = 30

    def _plan_paid_compensation(self, transactions: List[TaxTransaction], sheltered: Optional[List[TaxTransaction]], affiliated: Optional[List[TaxTransaction]], *,
                                cross_asset: bool = False,
                                detect_wash_sales: bool = True,
                                per_account_basis: bool = False,
                                loss_overrides: Sequence[Dict[str, Any]] = (),
                                filed_rows: Sequence[Dict[str, Any]] = ()
                                ) -> Dict[str, Any]:
        """Where the dividend compensation paid on shorts goes (§263(h);
        US-INC-04, backlogs TJ-1024, TJ-1059): a silent run of the prepare
        and the walk over copies of the rows, then the payments plan over
        the walk's short-cover chunks (not the report), finds which short
        shares each payment fell on and when they closed. {} with no such
        payment.
        A wash match can split a replacement short but moves no share
        between lots, so the plan holds for the walk that books it.
        `per_unit`: {(cover row id, short lot id): amount per share
        covered}; `payments`: {payment row id: {capitalized, on_short,
        open: [...]}}. A walk that raises plans nothing: the booking
        walk raises the same error, with its messages."""
        if not any(_us_paid_in_lieu(t) is not None for t in transactions):
            return {}
        plan: Dict[str, Any] = {'building': True, 'per_unit': {},
                                'payments': {}}
        # The engine's said-once notes are not said here: what this walk
        # discards the booking walk must still say (backlog TJ-1261).
        from taxjson.lib import markets
        from taxjson.lib.corporate_timeline import _SPLIT_DATE_NOTES
        said = ((markets._NOTED, set(markets._NOTED)),
                (_SPLIT_DATE_NOTES, set(_SPLIT_DATE_NOTES)))
        # The prepare, the walk and the payments plan: not the report
        # (step 4q). The settings the planner is handed are the booking
        # run's; the rest are `compute_gains`'s defaults (pinned against
        # its signature).
        kwargs: Dict[str, Any]          # the prepare's keywords
        kwargs = dict(
            transactions=[copy.copy(t) for t in transactions],
            sheltered_transactions=[copy.copy(t) for t in sheltered or []],
            affiliated_transactions=[copy.copy(t) for t in affiliated or []],
            _paid_comp=plan, cross_asset=cross_asset,
            detect_wash_sales=detect_wash_sales,
            per_account_basis=per_account_basis, loss_overrides=loss_overrides,
            trace=False, year_end=None, year_end_basis=None, cost_timeline=None,
            lots_at=None, lots_at_basis=None, filed_rows=filed_rows)
        # A drifted call fails here, loudly: the silent run below swallows
        # what the booking walk would raise again, never a signature
        # mismatch (TJ-2047).
        inspect.signature(self._prepare).bind(**kwargs)
        try:
            with contextlib.redirect_stderr(io.StringIO()), \
                    contextlib.redirect_stdout(io.StringIO()):
                run = self._prepare(**kwargs)
                _us_walk_rows(run=run)
        except Exception as e:
            if M.money_error(e):
                raise       # a money site not yet converted (TJ-3064, TJ-3100)
            return {}
        finally:
            for store, before in said:
                store.clear()
                store.update(before)
        # The plan itself is outside the silent run: the booking run never
        # repeats it, so an error in it is raised here, not planned away
        # (TJ-2056).
        _us_plan_payments(run=run)
        return {'per_unit': plan['per_unit'], 'payments': plan['payments']}

    @M.engine
    def compute_gains(self, transactions: List[TaxTransaction], sheltered_transactions: Optional[List[TaxTransaction]] = None, affiliated_transactions: Optional[List[TaxTransaction]] = None, cross_asset: bool = False, trace: bool = False, detect_wash_sales: bool = True, per_account_basis: bool = False, loss_overrides: Sequence[Dict[str, Any]] = (), year_end: Optional[str] = None, year_end_basis: Optional[str] = None, cost_timeline: Optional[str] = None, lots_at: Optional[str] = None, lots_at_basis: Optional[str] = None, _paid_comp: Optional[Dict[str, Any]] = None, filed_rows: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
        _run = self._prepare(
            _paid_comp=_paid_comp,
            affiliated_transactions=affiliated_transactions,
            cost_timeline=cost_timeline,
            cross_asset=cross_asset,
            detect_wash_sales=detect_wash_sales,
            filed_rows=filed_rows,
            loss_overrides=loss_overrides,
            lots_at=lots_at,
            lots_at_basis=lots_at_basis,
            per_account_basis=per_account_basis,
            sheltered_transactions=sheltered_transactions,
            trace=trace,
            transactions=transactions,
            year_end=year_end,
            year_end_basis=year_end_basis)
        lots = _us_walk_rows(run=_run)
        _us_say_retests(_run)

        return self._finish(lots=lots, run=_run)

    def _prepare(self, *, _paid_comp: Optional[Dict[str, Any]], affiliated_transactions: Optional[List[TaxTransaction]], cost_timeline: Optional[str], cross_asset: bool, detect_wash_sales: bool, filed_rows: Sequence[Dict[str, Any]], loss_overrides: Sequence[Dict[str, Any]], lots_at: Optional[str], lots_at_basis: Optional[str], per_account_basis: bool, sheltered_transactions: Optional[List[TaxTransaction]], trace: bool, transactions: List[TaxTransaction], year_end: Optional[str], year_end_basis: Optional[str]) -> _USRun:
        """The US engine's preamble: the input contract, the compensation plan (the
        planner's silent run on the booking run; given on the building run), the books
        named, deduped and indexed (`TaxRules`' shared stages), the corporate events, the
        replacement index, the inventories and the helpers bound; returns the run
        (`_USRun`, what the walk and the finish read)."""
        # `cost_timeline` ("trade" or "settle", the date basis its rows
        # are dated on): also return each account's lots (quantity,
        # basis) after every row that changed them (`cost_timeline`,
        # lib/cost_timeline; backlog TJ-0417). `lots_at` (YYYY-MM-DD,
        # read on `lots_at_basis` as year_end on year_end_basis): also
        # return every lot held at the end of that day, each on its own
        # (`lots_at`): the year-end record keeps the FIFO order and each
        # lot's basis, which the pools' totals lose (backlog TJ-0554).
        _check_engine_allowed(self.country)  # test-only guard (rules/country)
        # The computation's input contract (issues #14, #16; as Canada).
        require_computable_rows(transactions, sheltered_transactions,
                                affiliated_transactions)
        # Dividend compensation paid on a short (§263(h); US-INC-04) can
        # be a cover's cost: known only once the lots are walked, and
        # needed before that cover's §1091 test (backlog TJ-1059).
        if _paid_comp is None:
            _paid_comp = self._plan_paid_compensation(
                transactions, sheltered_transactions,
                affiliated_transactions, cross_asset=cross_asset,
                detect_wash_sales=detect_wash_sales,
                per_account_basis=per_account_basis,
                loss_overrides=loss_overrides, filed_rows=filed_rows)
        _comp_building = bool(_paid_comp.get('building'))
        _comp_per_unit: Dict[Tuple[RowId, RowId], Decimal] = _paid_comp.get('per_unit') or {}
        _comp_payments: Dict[RowId, Dict[str, Any]] = (
            _paid_comp.get('payments') or {})
        # (entry, cover row id, short lot id) of each cover, in the
        # planning run (_us_plan_payments).
        _short_cover_chunks: List[Tuple[_GainRow, RowId, RowId]] = []
        _taxable, sheltered_transactions, affiliated_transactions = \
            _by_account_name(transactions, sheltered_transactions,
                             affiliated_transactions)
        assert _taxable is not None, "a book in, a list out (only a None book stays None)"
        transactions = _taxable
        transactions, sheltered_transactions, affiliated_transactions = \
            renames_at_open((transactions, sheltered_transactions or [],
                             affiliated_transactions or []))
        _disambiguate_duplicate_ids(transactions, sheltered_transactions,
                                    affiliated_transactions)
        # §1091 contemplates a narrower "related party" rule than CRA's
        # affiliated-persons test, but the mechanics are the same: an
        # affiliated person's BUY/SELL is treated as a replacement for
        # wash-sale purposes. We additively merge both pools — passing
        # neither flag yields the baseline single-taxpayer behavior.
        # A merger's per-account EMPIRICAL ratios (US-CORP-14): with
        # per-account lots each row applies to its own account
        # (_per_account_rename_ratios); with symbol-wide lots the rows
        # fold into one event at the holdings-weighted ratio, as the
        # Canada engine's symbol-global pool does.
        if per_account_basis:
            _acct_ratios = _per_account_rename_ratios(
                list(transactions), list(sheltered_transactions or []),
                list(affiliated_transactions or []))
        else:
            _acct_ratios = {}
            transactions, sheltered_transactions, affiliated_transactions = \
                _fold_per_account_rename_ratios(
                    list(transactions), list(sheltered_transactions or []),
                    list(affiliated_transactions or []),
                    profile='us_main', date_of=lambda t: t.date)
        transactions, sheltered_transactions, affiliated_transactions = \
            self._dedupe_splits(transactions, sheltered_transactions,
                                affiliated_transactions)

        _split_covers = functools.partial(_us_split_covers, _acct_ratios=_acct_ratios)

        sheltered_ids = {t.id for t in sheltered_transactions}
        affiliated_ids = {t.id for t in affiliated_transactions}
        epsilon = _US_EPSILON

        get_sort_date = _us_get_sort_date

        non_capital = _us_non_capital

        # ASSIGN must be processed before BUYSELL when they share a settle
        # date+time — the option-leg ASSIGN sets up a pending adjustment
        # that the stock-leg BUYSELL then consumes.
        # Ordering centralized in lib/corporate_timeline.event_sort_key,
        # profile 'us_main': (trade date, time, priority) — priority sorts
        # AFTER time here, a deliberate divergence from Canada's phase
        # ladder, pinned in test_event_sort_key.py.

        # Use the module-level OCC parser. Local binding kept for
        # backward compatibility with this scope's existing callers.
        option_underlying = parse_option_underlying

        # Combined timeline so future sheltered openings can match against
        # earlier taxable losses — sheltered matches result in permanent
        # disallowance (Rev. Rul. 2008-5).
        all_events = sorted(
            transactions + (sheltered_transactions or []) + (affiliated_transactions or []),
            key=lambda x: event_sort_key(x, profile='us_main',
                                         date_of=get_sort_date),
        )
        # A lot's acquisition time, by its source row's id (lots carry
        # the id, not the time): the rename merge orders same-date lots
        # by it.
        _lot_time = {t.id: (t.time or '') for t in all_events}
        # Rows below the lot epsilon (1e-8 units) are not booked; they
        # are named once instead of vanishing silently (audit S070-09).
        _us_dust: List[TaxTransaction] = []
        # A lot residue of at most epsilon units folded into the sale
        # that closes the lot, and a sale's excess of at most epsilon
        # over the lots (no position opened): named too (US-CRYPTO-08,
        # re-audit A2-0808 / A2-1485). (symbol, units) per case; float
        # noise (under 1e-11 of the quantities) is not named.
        _us_dust_absorbed: List[Tuple[str, float]] = []
        _us_retests: List[Tuple[_GainRow, TaxTransaction, Decimal, Dict[str, Any]]] = []   # rows a wash add left at a loss (TJ-3428)
        _us_dust_dropped: List[Tuple[str, float]] = []
        _dust_noise = _us_dust_noise
        # Notes tied to one dated row (stock dividends, an unapplied
        # basis adjustment): printed now by a direct caller, or by
        # run_gains after its year filter (audit A2-0956: a 2023 stock
        # dividend's note landed in every later year's .sum).
        _dated_notes: List[Tuple[str, str]] = []

        emit_stderr = self.emit_replacement_stderr
        window_days = self.WASH_WINDOW_DAYS

        _nkey = functools.partial(_us_nkey, per_account_basis=per_account_basis)
        # Per-symbol split timeline (TRADE-basis dates, IRS semantics), ALL
        # scopes — a split is a property of the security, and
        # _dedupe_corporate_splits at entry guarantees one row per event.
        # Replacement records stay denominated in their own trade-date units
        # forever; _rep_units_factor converts between a rep's units and the
        # loss-date's units at match time. Built in all_events order (rename
        # migration is positional); the non_capital filter mirrors this
        # pre-pass loop's own skip.
        split_timeline = SplitTimeline.from_transactions(
            ev for ev in all_events if not non_capital(ev.action, ev.type))
        _rep_key = functools.partial(_us_rep_key, split_timeline=split_timeline)
        _warn_ticker_reused_after_rename(
            all_events, lambda ev: ev.date,
            rule_text="the wash-sale rule (US-BASIS-06)")

        long_replacements, short_replacements, _other_stkdivs = _us_scan_events(
            _nkey=_nkey, _rep_key=_rep_key, _split_covers=_split_covers,
            affiliated_ids=affiliated_ids, all_events=all_events, epsilon=epsilon,
            non_capital=non_capital, per_account_basis=per_account_basis,
            sheltered_ids=sheltered_ids)

        # === MAIN PASS STATE ===
        inventory_long: _USInventory = {}
        inventory_short: _USInventory = {}
        realized_gains: List[Dict[str, Any]] = []
        wash_sale_records: List[Dict[str, Any]] = []
        rules: RuleTable = {}       # each record's rule, noted as the walk makes it
        symbol_currency = _us_symbol_currencies(affiliated_transactions=affiliated_transactions,
                                                sheltered_transactions=sheltered_transactions,
                                                transactions=transactions)
        # When an option closes via ASSIGN/exercise, the premium is rolled
        # into the underlying stock's basis/proceeds rather than recognized
        # as a separate gain (IRS Pub 550). Key: underlying symbol, value:
        # negative of the would-be gain on the option close, matching the
        # Canada engine's sign convention (so SELL adds, BUY subtracts).
        # (The staging ledger itself is built below, once the main
        # pass's sorted stream exists — see _AssignPremiumLedger.)

        # Each replacement list's index (TJ-0238): the scans below read
        # the records of a date window, of one row id, or holding one
        # lot, instead of walking (and date-parsing) the whole class per
        # sale. The lists are fixed from here on.
        _rep_ix: Dict[int, _RepIndex] = {}
        _rep_home: Dict[int, _RepIndex] = {}
        for _rl in (list(long_replacements.values())
                    + list(short_replacements.values())):
            _rep_ix[id(_rl)] = _ix_ = _RepIndex(_rl)
            for _r in _rl:
                _rep_home[id(_r)] = _ix_

        # §1091 has no still-held test: a purchase in another of your
        # TAXABLE accounts inside the window replaces the loss even when
        # that account sold the shares before the loss sale (tax-logic
        # US-WASH-22, owner decision on audit A2-0544). Its sold,
        # never-matched shares are kept here per purchase, each with
        # the gain row of the sale that closed them, so a later loss
        # can add its disallowed amount to that sale's basis (the
        # §1091(d) basis of shares no longer held changes their sale).
        _long_rep_of = _us_taxable_rep_of(long_replacements)
        _short_rep_of = _us_taxable_rep_of(short_replacements)  # §1091(e), TJ-2708

        # A §1256 contract (a futures contract, an option on one, a
        # broad-based index option) is marked to market, outside §1091.
        # Its loss is never denied; a re-purchase in the window is
        # flagged for a manual check (tax-logic US-WASH-18; audit
        # A2-0053 — a re-bought F:CLG7 had its whole 10,000 loss
        # disallowed with no flag; TJ-0506 — an SPX option's too).
        # Canada's s.54 covers any property and keeps denying.
        _fut_flags: Dict[RowId, Dict[str, Any]] = {}
        _outside_seen: Dict[str, bool] = {}

        # === MAIN PASS ===
        # Each option ASSIGN's own stock leg is paired by identity in
        # the premium ledger (_pair_assign_legs; see the Canada twin),
        # keyed per (ACCOUNT, symbol) so in a blended combined book one
        # account's leg never absorbs another account's premium.
        _assign_underlying = _make_assign_underlying_resolver(
            transactions, lambda _t: _t.date)
        # (ACCOUNT, underlying) pairs that trade as STOCK in the
        # taxable book — an ASSIGN whose underlying is absent for ITS
        # OWN account is cash-settled; see the Canada twin (OB
        # excluded: an OB-only underlying means the stock leg is
        # missing).
        taxable_stock_symbols = {
            (t.account, t.symbol) for t in transactions
            if not is_option_symbol(t.symbol)
            and t.action in ('BUYSELL', 'ASSIGN')}
        taxable_sorted = sorted(
            transactions,
            key=lambda x: event_sort_key(x, profile='us_main',
                                         date_of=get_sort_date))
        # A leg dated before its option row gets the option moved in
        # front of it (A2-0051/0195).
        _assign_pairs = _pair_assign_legs(
            taxable_sorted, lambda _t: True, _assign_underlying)
        taxable_sorted = _place_assign_options(taxable_sorted,
                                               _assign_pairs)
        # The user's filing positions against §1091 (.tt ALLOWLOSS,
        # lib/loss_overrides; US-WASH-25): the named sale's loss is
        # matched as usual — so the replacement shares it would take are
        # still taken and every other loss's verdict is unchanged — but
        # nothing is disallowed, no basis is added and no holding period
        # is tacked; what §1091 would have disallowed is recorded.
        _ov_plan = _LO.plan(loss_overrides, taxable_sorted)
        _ov_would: Dict[RowId, Dict[str, Any]] = {}

        pending_option_adjustments = _AssignPremiumLedger(
            taxable_sorted,
            lambda _t: (not is_option_symbol(_t.symbol)
                        and not exercise_target(_t)
                        and _t.action in ('BUYSELL', 'ASSIGN')),
            _assign_pairs)

        (_disp_member, _disp_total) = _us_disposition_groups(
            epsilon=epsilon, non_capital=non_capital, taxable_sorted=taxable_sorted)

        _ikey = functools.partial(_us_ikey, per_account_basis=per_account_basis)

        # §355 spin-offs booked per parent lot (US-CORP-07): the spun-off
        # shares' BUYSELL and the parent's ADJUST of one event and
        # account, joined by corp_event_id. The BUYSELL sorts first at
        # their shared moment and books both; its ADJUST is then skipped.
        _spin_adj: Dict[Tuple[str, str], TaxTransaction] = {}
        for _t in taxable_sorted:
            if (_t.action == 'ADJUST' and _t.type == SPINOFF_355_TYPE
                    and _t.corp_event_id):
                _spin_adj.setdefault((_t.account, _t.corp_event_id), _t)
        # §356 boot exchanges booked per block (US-CORP-05): the SELL of
        # the old shares (sorted first) leaves its blocks here for the
        # BUY of the new shares, keyed (account, corp_event_id).
        # Own-account custody moves (US-BASIS-05): each TRANSFER lot_move
        # leg -> its other leg (out of one account, into another: same
        # symbol, moment, quantity and description).
        _move_pair: Dict[RowId, TaxTransaction] = {}
        _move_ins = [t for t in taxable_sorted
                     if t.action == 'TRANSFER' and t.type == LOT_MOVE_TYPE
                     and (t.quantity or 0.0) > 0]
        for _o in taxable_sorted:
            if not (_o.action == 'TRANSFER' and _o.type == LOT_MOVE_TYPE
                    and (_o.quantity or 0.0) < 0):
                continue
            for _i in _move_ins:
                if (_i.id not in _move_pair and _i.account != _o.account
                        and _i.symbol == _o.symbol and _i.date == _o.date
                        and (_i.time or '') == (_o.time or '')
                        and _i.description == _o.description
                        and abs(_i.quantity + _o.quantity)
                        <= 1e-9 * max(1.0, abs(_i.quantity))):
                    _move_pair[_o.id] = _i
                    _move_pair[_i.id] = _o
                    break

        # A native book's journals between two listings (TJ-0361):
        # one leg books the pair (_carry_lots).
        _jlegs = _journal_legs(taxable_sorted)
        for _jo, _ji in _jlegs.values():
            for _jt in (_jo, _ji):
                if _jt.currency:
                    symbol_currency.setdefault(_jt.symbol, _jt.currency)

        _row_after = functools.partial(_us_row_after, get_sort_date=get_sort_date, taxable_sorted=taxable_sorted)

        _ye_next: Optional[TaxTransaction] = (
            _row_after(year_end, year_end_basis) if year_end else None)
        _la_next: Optional[TaxTransaction] = (
            _row_after(lots_at, lots_at_basis) if lots_at else None)

        # The cost timeline (lib/cost_timeline; backlog TJ-0417): each
        # row's own lots are read after it (at the next row), and every
        # account's lots held at the end of each day — a basis addition
        # can land in another account's lot.
        _tl = None
        _tl_dates: List[Tuple[str, str]] = []

        _us_state = functools.partial(_us_tl_state, _ikey=_ikey, inventory_long=inventory_long, inventory_short=inventory_short, symbol_currency=symbol_currency)

        if cost_timeline:
            from taxjson.lib import cost_timeline as _CT
            _tl = _CT.Recorder(_us_state)

        _ev_pos = {id(_e): _i for _i, _e in enumerate(all_events)}
        _run = _USRun(
            _acct_ratios=_acct_ratios,
            _assign_underlying=_assign_underlying,
            _comp_building=_comp_building,
            _comp_payments=_comp_payments,
            _comp_per_unit=_comp_per_unit,
            _dated_notes=_dated_notes,
            _disp_member=_disp_member,
            _disp_total=_disp_total,
            _dust_noise=_dust_noise,
            _ev_pos=_ev_pos,
            _fut_flags=_fut_flags,
            _jlegs=_jlegs,
            _la_next=_la_next,
            _long_rep_of=_long_rep_of,
            _short_rep_of=_short_rep_of,
            _lot_time=_lot_time,
            _move_pair=_move_pair,
            _other_stkdivs=_other_stkdivs,
            _outside_seen=_outside_seen,
            _ov_plan=_ov_plan,
            _ov_would=_ov_would,
            _paid_comp=_paid_comp,
            _rep_home=_rep_home,
            _rep_ix=_rep_ix,
            _short_cover_chunks=_short_cover_chunks,
            _spin_adj=_spin_adj,
            _tl=_tl,
            _tl_dates=_tl_dates,
            _us_dust=_us_dust,
            _us_dust_absorbed=_us_dust_absorbed,
            _us_retests=_us_retests,
            filed_rows=filed_rows,
            _us_dust_dropped=_us_dust_dropped,
            _ye_next=_ye_next,
            all_events=all_events,
            cost_timeline=cost_timeline,
            detect_wash_sales=detect_wash_sales,
            emit_stderr=emit_stderr,
            epsilon=epsilon,
            get_sort_date=get_sort_date,
            inventory_long=inventory_long,
            inventory_short=inventory_short,
            long_replacements=long_replacements,
            lots_at=lots_at,
            non_capital=non_capital,
            option_underlying=option_underlying,
            pending_option_adjustments=pending_option_adjustments,
            per_account_basis=per_account_basis,
            realized_gains=realized_gains,
            rules=rules,
            short_replacements=short_replacements,
            split_timeline=split_timeline,
            symbol_currency=symbol_currency,
            taxable_sorted=taxable_sorted,
            taxable_stock_symbols=taxable_stock_symbols,
            trace=trace,
            transactions=transactions,
            wash_sale_records=wash_sale_records,
            window_days=window_days,
            year_end=year_end,
            year_end_basis=year_end_basis)
        return _run

    def _finish(self, *, lots: Tuple[Optional[_USLotsNow], Optional[_USLotsNow]], run: _USRun) -> Dict[str, Any]:
        """The US engine's report once the rows are walked: the dust ledger's notes, the
        compensation plan's payments, the override stamps, the option-replacement
        warnings, the lot and inventory rows, the cost timeline; returns `compute_gains`'s
        dict; `lots` is the walk's pair (the lots at `lots_at`, at the year end)."""
        _la_lots, _ye_lots = lots
        _us_dividend_entries(run=run)

        # Invariant: across all close events (long and short), the
        # difference between allowed and raw gain must equal the total
        # disallowed amount. Permanently disallowed losses also add back.
        sell_entries = [g for g in run.realized_gains if g.get('action') not in ('DIVIDEND', 'DIVIDEND_IN_LIEU')]
        sum_allowed = sum(g['gain'] for g in sell_entries)
        sum_raw = sum(g.get('raw_gain', g['gain']) for g in sell_entries)
        sum_disallowed = sum(g.get('disallowed_amount', _ZERO) for g in sell_entries)
        if abs((sum_allowed - sum_raw) - sum_disallowed) > _CENT:
            emit_line(f"warning: US gain disallowance invariant broken — "
                f"allowed-raw={sum_allowed - sum_raw:.4f} vs disallowed={sum_disallowed:.4f}")
        _warn_undrained_adjustments(run.pending_option_adjustments.undrained(), self.country)
        if run._us_dust:
            _by: Dict[str, List[float]] = {}
            for _t in run._us_dust:
                _by.setdefault(_t.symbol, []).append(_t.quantity)
            emit_line("warning: rows smaller than 1e-08 units are not booked "
                  "by the US engine: " + ", ".join(
                      f"{s_} ({len(q)} row(s), net {sum(q):+.3g})"
                      for s_, q in sorted(_by.items()))
                  + " — their units and money are left out of the lots "
                  "and Form 8949.")
            # Money left out is a conservation problem, not dust: said in
            # that channel too, so `sum --json`'s readiness counts it
            # (2026-10 critique, the US twin of audit #101).
            _money: Dict[str, Decimal] = {}
            for _t in run._us_dust:
                _money[_t.symbol] = (_money.get(_t.symbol, _ZERO)
                                     + abs(M.of(_t.net_amount or 0.0)))
            _material = {k: v for k, v in _money.items() if v >= _CENT}
            if _material:
                emit_line("warning: conservation: rows under 1e-08 units "
                          "carrying money are left out of the US lots: "
                          + ", ".join(f"{k} {v:,.2f}"
                                      for k, v in sorted(_material.items()))
                          + " (their buy and sale amounts) — enter the "
                          "position with its real units.")
        for _what, _rows in (
                ("a lot residue of at most 1e-08 units was folded into "
                 "the sale that closed the lot (its cost is in that "
                 "sale's basis)", run._us_dust_absorbed),
                ("a sale exceeded the units held by at most 1e-08 (no "
                 "position was opened for the excess; the whole proceeds "
                 "are on the units held)", run._us_dust_dropped)):
            if _rows:
                _agg: Dict[str, List[float]] = {}
                for _s, _q in _rows:
                    _agg.setdefault(_s, []).append(_q)
                emit_line(f"warning: {_what}: " + ", ".join(
                    f"{s_} ({len(q)} time(s), {sum(q):+.3g} units)"
                    for s_, q in sorted(_agg.items()))
                    + " (US-CRYPTO-08).")

        # Warn-only call-as-replacement scan (the experimental US engine
        # does not enforce it; always on — cross_asset is retired).
        # Losses on the TRADE basis, matching the §1091 window
        # convention. No held-at-end requirement in §1091 (that is a CRA
        # s.54 condition).
        _orw_src = [g for g in sell_entries
                    if g.get('raw_gain', g['gain']) < _MINUS_HALF_CENT
                    and not g.get('tainted')]
        _orw_losses = [
            {'symbol': g['symbol'],
             'date': g['date'],
             'qty': abs(float(g.get('qty') or 0.0)),
             'amount': g.get('raw_gain', g['gain']),
             'id': g.get('id', ''),
             'direction': g.get('direction', 'LONG')}
            for g in _orw_src]

        _open_qty = _us_open_qty
        _orw_calls = [dict(l, open_qty=_open_qty(g))
                      for l, g in zip(_orw_losses, _orw_src)]
        option_replacement_warnings = detect_option_replacement_matches(
            _orw_calls, run.all_events,
            date_of=lambda t: t.date,
            canonical=run.split_timeline.canonical,
            statute_label="IRS §1091 ('option to acquire')",
            check_held_at_end=False)
        option_replacement_warnings += detect_right_replacement_matches(
            _orw_losses, run.all_events,
            date_of=lambda t: t.date,
            canonical=run.split_timeline.canonical,
            statute_label="IRS §1091 ('contract or option to acquire')")
        option_replacement_warnings += \
            detect_unresolved_option_replacement_matches(
                _orw_losses, run.all_events,
                date_of=lambda t: t.date,
                canonical=run.split_timeline.canonical,
                statute_label="IRS §1091 ('option to acquire')")
        option_replacement_warnings += detect_opening_lot_matches(
            _orw_losses, run.all_events, date_of=lambda t: t.date,
            canonical=run.split_timeline.canonical,
            statute_label="IRS §1091")
        option_replacement_warnings += list(run._fut_flags.values())
        self._emit_replacement_warnings(option_replacement_warnings)

        # Conservation post-condition (see _verify_share_conservation).
        _inv_qty: Dict[str, float] = {}
        for _k, _lots in run.inventory_long.items():
            _sym: str = _k[1] if run.per_account_basis else _k  # type: ignore[assignment]  # code: the symbol by the key's shape, not by the basis flag (TJ-4117)
            _inv_qty[_sym] = _inv_qty.get(_sym, 0.0) \
                + sum(l['qty'] for l in _lots)
        for _k, _lots in run.inventory_short.items():
            _sym = _k[1] if run.per_account_basis else _k  # type: ignore[assignment]  # as above (TJ-4117)
            _inv_qty[_sym] = _inv_qty.get(_sym, 0.0) \
                - sum(l['qty'] for l in _lots)
        _verify_share_conservation(
            [t for t in run.taxable_sorted
             if (not run.non_capital(t.action, t.type)
                 and t.action in ('BUYSELL', 'ASSIGN', 'OPENING_BALANCE',
                                  'SPLIT'))
             or (t.action == 'TRANSFER' and t.type == LOT_MOVE_TYPE)
             or _is_listing_journal(t)],
            _inv_qty, self.country,
            zero_ratio_skips=True)      # US: ratio-0 skips the scale,
                                        # rename still migrates

        by_ticker: Dict[str, Dict[str, Any]] = {}
        for g in run.realized_gains:
            # Mirror the Canada engine: tainted dispositions (missing-history
            # OPENING_BALANCE consumption) carry fabricated cost=0
            # numbers and the CLI splits them out into
            # `manual_reporting_required` after this point. Letting them
            # into by_ticker totals would silently inflate any
            # downstream consumer reading by_ticker rather than
            # transactions.
            if g.get('tainted'):
                continue
            s = g['symbol']
            if s not in by_ticker:
                by_ticker[s] = {'total_cost': _ZERO, 'total_proceeds': _ZERO, 'total_gain': _ZERO, 'total_div': _ZERO, 'total_pil': _ZERO, 'trade_count': 0, 'hold_days': []}
            stats = by_ticker[s]
            if g.get('action') == 'DIVIDEND':
                stats['total_div'] += g.get('dividend', _ZERO)
            elif g.get('action') == 'DIVIDEND_IN_LIEU':
                stats['total_pil'] += g.get('pil', _ZERO)
            else:
                stats['total_cost'] += g['cost']
                stats['total_proceeds'] += g['proceeds']
                stats['total_gain'] += g['gain']
                stats['trade_count'] += 1
                stats['hold_days'].append(g.get('days_held', 0))

        _lot_rows = functools.partial(_us_lot_rows, epsilon=run.epsilon, per_account_basis=run.per_account_basis)

        _inventory_rows = functools.partial(_us_inventory_rows, epsilon=run.epsilon, per_account_basis=run.per_account_basis, symbol_currency=run.symbol_currency)

        inventory_report = _inventory_rows(run.inventory_long, run.inventory_short)

        _ov_items = self._stamp_overrides(run._ov_plan, run._ov_would, run.realized_gains)
        _tl_basis = ''          # the timeline's date basis, read with `run._tl` only
        if run._tl is not None:
            assert run.cost_timeline is not None, "a timeline is kept only with cost_timeline set (_prepare)"
            _tl_basis = run.cost_timeline
        return {
            **({'loss_overrides': _ov_items} if _ov_items is not None else {}),
            'transactions': run.realized_gains,
            'by_ticker': by_ticker,
            'wash_sales': run.wash_sale_records,
            'option_replacement_warnings': option_replacement_warnings,
            'dated_notes': [list(n) for n in run._dated_notes],
            'inventory': inventory_report,
            # The lots at the end of year_end (see _ye_lots), in the
            # inventory's shape.
            **({'inventory_year_end': _inventory_rows(*_ye_lots)}
               if _ye_lots is not None else {}),
            # The date the year end was read on, when not the walk's
            # trade date (TJ-0343): edge-cases says which otherwise.
            **({'inventory_year_end_basis': 'settle'}
               if _ye_lots is not None and run.year_end_basis == 'settle'
               else {}),
            # Every lot held at the end of lots_at, one by one, in
            # FIFO order (backlog TJ-0554).
            **({'lots_at': _lot_rows(*_la_lots)}
               if _la_lots is not None else {}),
            # Each account's lots after every row that changed them
            # (backlog TJ-0417): a day's lots are every row up to the
            # last one dated on or before it, as _ye_lots.
            **({'cost_timeline': run._tl.finish(
                run._tl_dates, basis=_tl_basis,
                stamp='min' if run.cost_timeline != 'settle' else 'pool')}
               if run._tl is not None else {}),
            **({'rules': run.rules} if run.rules else {}),    # (engine/rows RuleTable)
            'summary': {
                'total_gain': sum(g['gain'] for g in run.realized_gains),
                'total_disallowed': sum(w['disallowed_amount'] for w in run.wash_sale_records),
                'count': len(run.realized_gains),
            },
        }
