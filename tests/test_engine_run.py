"""`_CanadaRun` holds what the Canadian engine's fixed-point loop reads but
never rebinds (step 4g of the engine refactor, backlog TJ-1843, TJ-1913,
TJ-1915, TJ-1916): no field is rebound between the holder's build and the
loop's end, no helper on it reads a name the loop rebinds, each function
reads its own fields and nothing else. Read from the source, so a field the
loop starts to rebind per iteration (which the frozen snapshot would hide)
fails here. Step 4h's `_SLPass`, `_SLLoss` and `_SLHeld` are pinned the same
way: their fields are what the denial pass's four parts read. Step 4m's
walk parts (`_walk_adjust`, `_walk_split`, `_walk_trade`) read `run` and
`it` beside the walk and take the row's locals they read, nothing more."""
import ast
import builtins
import inspect
import unittest

from _free_names import free_names
import _engine
from taxjson.engine.us.taxrules import USATaxRules

PASS_FIELDS = sorted([
    "_book_ids", "_disp_last_seen", "_ev_memo", "_opt_lineage", "_opt_prev", "_ov_plan",
    "_rows_of_cls", "_short_routed", "_sl_idx", "_split_syms", "adjust_to_trigger",
    "affiliated_ids", "all_txs", "detect_wash_sales", "final_virtual_txs",
    "get_sort_date", "loss_to_trigger", "loss_to_triggers_multi",
    "permanent_by_loss", "row_cls", "rules", "sheltered_ids",
    "split_timeline", "wash_place", "wash_windows"])
WALK_FIELDS = sorted([
    "_assign_underlying", "_grant_mode", "_lot_seq", "_move_notes", "_short_routed", "affiliated_ids",
    "cost_timeline", "final_virtual_txs", "get_sort_date", "grant_on_trade",
    "option_buyback_loss_superficial", "option_grant_since", "sheltered_ids", "taxable_ids",
    "taxable_stock_symbols", "trace", "year_end", "year_end_basis"])
# The run's module helpers (step 4r, TJ-1964): each takes `run` first and reads its
# inputs from it; a stage that calls one reads those fields through it.
RUN_HELPERS = ("_ca_ev_key", "_ca_write_year", "_ca_grant_applies", "_ca_short_lot_open",
               "_ca_open_short_option", "_ca_opt_root", "_ca_row_pool_key",
               "_ca_walk_date", "_ca_stock_short_key", "_ca_tl_after",
               "_walk_move_legs", "_walk_year_end_legs", "_conserved_rows")
# The iteration's helpers (step 4r, slice 2): `run` then `it` first.
ITERATION_HELPERS = ("_ca_walk_date", "_ca_stock_short_key", "_ca_tl_after",
                     "_walk_move_legs", "_walk_year_end_legs")
# The denial pass's helpers (step 4r, slice 3): over `pas` and `run`, or over a
# fact handed to them (`_sl_pkey(run, pos, t)`, `_sl_call_und(cls_root, sym)`).
PASS_HELPERS = ("_sl_holder", "_sl_holder_rank", "_sl_pkey", "_sl_call_und",
                "_sl_symbol_asof", "_sl_opening_qty", "_sl_wpos", "_sl_class_balances",
                "_own_move_held")
# The run's callable fields that are inputs from outside the engine (the settings
# layer's date basis and row class, the book's assign resolver), not its own helpers.
RUN_CALLABLE_INPUTS = ("_assign_underlying", "alias_of", "get_sort_date", "row_cls")
# The solve stage (step 4o, `_ca_solve`: the loop itself) reads these.
SOLVE_FIELDS = sorted([
    "_assign_underlying", "_move_legs", "_short_routed", "affiliated_ids", "all_txs",
    "cost_timeline", "detect_wash_sales", "final_virtual_txs", "get_sort_date",
    "loss_to_trigger", "loss_to_triggers_multi", "permanent_by_loss", "sheltered_ids",
    "trace", "wash_place", "wash_windows"])
# The finish stage (step 4o, `CanadaTaxRules._finish`: the report) reads these.
FINISH_FIELDS = sorted([
    "_ev_memo", "_ov_plan", "affiliated_ids", "alias_of", "all_txs", "cost_timeline",
    "final_virtual_txs", "get_sort_date", "loss_to_trigger", "loss_to_triggers_multi",
    "permanent_by_loss", "rules", "sheltered_ids", "taxable_ids", "transactions",
    "wash_windows", "year_end_basis"])


PARTS = ("_sl_trigger_scan", "_sl_window_walk", "_sl_window_trace", "_sl_allocate")
# The walk's action branches as functions (step 4m): they read `run` and `it`
# beside the walk, and take the row's locals as keyword-only inputs.
WALK_PARTS = ("_walk_adjust", "_walk_split", "_walk_trade",
              # an exercise's route: its premium rolls into its shares, or cash-settled (TJ-5005)
              "_assign_rolls")
US_WALK_PARTS = ("_us_walk_lot_move", "_us_walk_split", "_us_walk_adjust",
                 "_us_walk_stock_dividend", "_us_walk_spinoff", "_us_walk_reorg",
                 "_us_walk_buy", "_us_walk_sell", "_us_gain_entry")
WALK_PART_INPUTS = {
    "_walk_adjust": ["is_other_scope", "it", "pool", "run", "symbol", "trace_sym", "tx"],
    "_assign_rolls": ["is_other_scope", "it", "run", "symbol", "tx"],
    "_walk_split": ["is_other_scope", "it", "pool", "qty", "run", "symbol", "tx"],
    "_walk_trade": ["account", "internal_adj", "internal_dw", "internal_tainted",
                    "is_option_assign", "is_other_scope", "it", "pool", "qty", "run",
                    "symbol", "trace_sym", "tx",   # trace_sym: the pool's own trace (TJ-3228)
                    "internal_sources"]}
US_WALK_PART_INPUTS = {
    "_us_walk_lot_move": ["_book_accounts", "_moves_done", "ikey", "run", "symbol",
                          "symbol_traces", "tx"],
    "_us_walk_split": ["_main_split_groups", "run", "symbol", "tx"],
    "_us_walk_adjust": ["ikey", "run", "symbol", "symbol_traces", "tx"],
    # (`_boot_blocks`: its `exchanged`, the §356 exchanges' old-share blocks, TJ-4987)
    "_us_walk_stock_dividend": ["_boot_blocks", "ikey", "run", "symbol", "symbol_traces", "tx"],
    "_us_walk_spinoff": ["_spin_done", "ikey", "run", "symbol", "symbol_traces", "tx"],
    "_us_walk_reorg": ["_boot_blocks", "ikey", "run", "symbol", "symbol_traces", "tx"],
    "_us_walk_buy": ["ikey", "is_option_assign", "run", "symbol", "symbol_traces", "tx",
                     "tx_net", "tx_qty_abs", "underlying_for_assign",
                     # no walk local: a delivery to a short hands it (`_us_deliver_to_short`, TJ-2738)
                     "delivered"],
    "_us_walk_sell": ["ikey", "is_option_assign", "run", "symbol", "symbol_traces", "tx",
                      "tx_net", "tx_qty_abs", "underlying_for_assign"],
    "_us_gain_entry": ["chunk_qty", "chunk_cost", "chunk_proceeds", "raw_gain", "allowed_gain",
                       "disallowed_amt", "permanently_disallowed_amt", "replacement_ids",
                       "wash_reps", "days_held", "is_long_term", "direction", "rg_trace",
                       "is_option_sym", "symbol", "tx", "tx_qty_abs", "tainted",
                       "raw_acq_date", "deferred", "ov_share",
                       "unknown_from"]}
# The walk's unit helpers read the loss's objects too (step 4j).
UNIT_HELPERS = ("_sl_pre_exists", "_sl_row_loss_units", "_sl_call_units", "_sl_avail_native",
                "_sl_gone_by_end")
# The allocation's helpers (TJ-2711, TJ-2720, TJ-2756, TJ-2761): a trigger's
# holder keys, a pool's rows in the window and whether a trigger's position is
# sold out, the sale's allocation over its triggers, the row the addition is
# placed after and its ADJUST row; over the loss's objects and the run, like
# the parts.
ALLOCATION_HELPERS = ("_sl_trigger_holder", "_sl_pool_rows", "_sl_sold_out",
                      "_sl_sale_alloc", "_sl_landing_row", "_sl_mk_adjust")
# The US engine's helpers since step 4r (TJ-1983): module functions taking `run` first.
US_RUN_HELPERS = ("_us_ikey_on", "_us_rkey_on", "_us_allot_sold_shares", "_us_apply_other_stkdivs", "_us_apply_sold_replacement", "_us_book_journal",
                  "_us_carry_lots", "_us_draw_long_lots", "_us_find_replacements_in_window", "_us_flag_futures_loss",
                  "_us_grow_rep_capacity", "_us_insert_lots", "_us_inv_keys_for", "_us_ix",
                  "_us_lots_copy", "_us_move_long_lots", "_us_note", "_us_outside_1091",
                  "_us_ov_loss", "_us_ov_take", "_us_plan_disposition", "_us_plan_draw",
                  "_us_record_sold_replacement", "_us_rep_units_factor", "_us_set_ref", "_us_sold_replacements_in_window",
                  "_us_split_gain_entry", "_us_split_rep_refs", "_us_take_option_adj", "_us_tl_held",
                  "_us_tl_label",
                  # shares that left with no sale (TJ-2700, TJ-2737)
                  "_us_record_left_shares", "_us_apply_left_replacement", "_us_left_lots_held",
                  # the exchange's row restated, the exercise's shares named (TJ-3427, TJ-3430)
                  "_us_restate_boot", "_us_name_delivered_lots",
                  # a row an add left at a loss, tested itself; the walks' held-match blocks (TJ-3428)
                  "_us_queue_retest", "_us_run_retests", "_us_retest_row",
                  "_us_apply_held_long", "_us_apply_held_short",
                  # each wash record's rules (PLAN step 11)
                  "_us_note_rules",
                  # a filed year's row left as filed (TJ-3468)
                  "_us_lock_row", "_us_would_wash",
                  # another account's closed replacements, both sides (TJ-2700, TJ-2708)
                  "_us_with_sold_replacements", "_us_match_sold_replacement",
                  "_us_split_matched_short",
                  # lots delivered to a short: a listing journal, an own move (TJ-2738, TJ-3424)
                  "_us_deliver_to_short",
                  # an ASSIGN's route: its premium rolls into its shares, or cash-settled (TJ-5005)
                  "_us_assign_route",
                  )
# Three the pre-run scan and the timeline recorder share stay pure over one keyword
# input, which the walk hands from the run.
US_PURE_HELPERS = ("_us_ikey", "_us_rep_key", "_us_split_covers")
# The pre-run consumers' partials the US `_prepare` keeps as locals (the year-end row,
# the event scan's three, the timeline recorder's state reader): none is a run field.
US_PREPASS_PARTIALS = ("_split_covers", "_nkey", "_rep_key", "_ikey", "_row_after", "_us_state")
# The run's callable fields that are inputs from outside the engine.
US_RUN_CALLABLE_INPUTS = ("_assign_underlying", "_dust_noise", "get_sort_date", "non_capital", "option_underlying")


def _module_names(tree):
    """What the module binds at its top (and the builtins): the names a part
    may read beside its inputs and its own locals."""
    out = set(dir(builtins))
    for st in tree.body:
        if isinstance(st, (ast.FunctionDef, ast.ClassDef)):
            out.add(st.name)
        else:
            out |= _stores(st)
    return out


def _call_stmt(func, name):
    """The statement of `func`'s body whose value is a call of `name` (a
    function or a method of self)."""
    for st in func.body:
        v = getattr(st, "value", None)
        if isinstance(v, ast.Call) and (getattr(v.func, "id", None) == name
                                        or getattr(v.func, "attr", None) == name):
            return st
    raise AssertionError(name)


def _loads(func):
    return {n.id for n in ast.walk(func)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def _reads(func, obj):
    return sorted({n.attr for n in ast.walk(func) if isinstance(n, ast.Attribute)
                   and isinstance(n.value, ast.Name) and n.value.id == obj})


def _reads_via(func, obj, helpers):
    """`_reads`, following the calls of a helper handed `obj` as a leading
    positional argument (`_ca_ev_key(run, t)`, `_ca_walk_date(run, it, t)`):
    what the helper reads of that parameter, the caller reads."""
    def walk(fn, name_of_obj, seen):
        out = set(_reads(fn, name_of_obj))
        for n in ast.walk(fn):
            name = getattr(n.func, "id", None) if isinstance(n, ast.Call) else None
            if name not in helpers:
                continue
            params = [a.arg for a in helpers[name].args.args]
            if params and params[0] == "self":          # a class: its __init__'s parameters
                params = params[1:]
            for i, a in enumerate(n.args[:2]):
                if isinstance(a, ast.Name) and a.id == name_of_obj and (name, i) not in seen:
                    seen.add((name, i))
                    out |= walk(helpers[name], params[i], seen)
            for k in n.keywords:                       # a holder handed by keyword (`pas=pas`)
                if isinstance(k.value, ast.Name) and k.value.id == name_of_obj and (name, k.arg) not in seen:
                    seen.add((name, k.arg))
                    out |= walk(helpers[name], k.arg, seen)
        return out
    return sorted(walk(func, obj, set()))


def _stores(node):
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            out.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            out |= {(a.asname or a.name).split(".")[0] for a in n.names}
        elif isinstance(n, (ast.Nonlocal, ast.Global)):
            out |= set(n.names)
    return out


class TestCanadaRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        t = ast.parse(_engine.source())
        cls.fields = [st.target.id for n in ast.walk(t)
                      if isinstance(n, ast.ClassDef) and n.name == "_CanadaRun"
                      for st in n.body if isinstance(st, ast.AnnAssign)]
        engine = [n for n in t.body if isinstance(n, ast.ClassDef)
                  and n.name == "CanadaTaxRules"][0]
        cls.cg = [n for n in engine.body if isinstance(n, ast.FunctionDef)
                  and n.name == "compute_gains"][0]
        # The shell is three stages (step 4o): `_prepare` builds the run,
        # `_ca_solve` is the loop, `_finish` the report.
        cls.prepare = [n for n in engine.body if isinstance(n, ast.FunctionDef)
                       and n.name == "_prepare"][0]
        cls.finish = [n for n in engine.body if isinstance(n, ast.FunctionDef)
                      and n.name == "_finish"][0]
        cls.solve = [n for n in t.body if isinstance(n, ast.FunctionDef)
                     and n.name == "_ca_solve"][0]
        cls.loop = [st for st in cls.solve.body if isinstance(st, ast.For)
                    and "iteration" in ast.unparse(st.target)][0]
        cls.prepare_call = _call_stmt(cls.cg, "_prepare")
        cls.solve_call = _call_stmt(cls.cg, "_ca_solve")
        cls.builds = [n for n in ast.walk(cls.prepare) if isinstance(n, ast.Call)
                      and getattr(n.func, "id", None) == "_CanadaRun"]
        cls.funcs = {n.name: n for n in ast.walk(t)
                     if isinstance(n, ast.FunctionDef)
                     and n.name in ("_superficial_loss_pass", "_walk_rows") + WALK_PARTS}
        cls.parts = {n.name: n for n in ast.walk(t)
                     if isinstance(n, ast.FunctionDef)
                     and n.name in PARTS + UNIT_HELPERS + ALLOCATION_HELPERS}
        cls.helpers = {n.name: n for n in t.body
                       if isinstance(n, ast.FunctionDef) and n.name in RUN_HELPERS + PASS_HELPERS}
        # The class balances take the run too (step 4r, slice 3's review): what its
        # constructor reads of the run, the pass reads.
        cls.helpers["_ClassBalances"] = [m for n in t.body if isinstance(n, ast.ClassDef)
                                         and n.name == "_ClassBalances" for m in n.body
                                         if isinstance(m, ast.FunctionDef) and m.name == "__init__"][0]
        # The year-end pools' reader takes the run too (TJ-4984): what its
        # constructor reads of the run, the walk reads.
        cls.helpers["_YearEndPools"] = [m for n in t.body if isinstance(n, ast.ClassDef)
                                        and n.name == "_YearEndPools" for m in n.body
                                        if isinstance(m, ast.FunctionDef) and m.name == "__init__"][0]
        cls.objects = {n.name: [st.target.id for st in n.body
                                if isinstance(st, ast.AnnAssign)]
                       for n in ast.walk(t) if isinstance(n, ast.ClassDef)
                       and n.name in ("_SLPass", "_SLLoss", "_SLHeld")}
        cls.module_names = _module_names(t)

    def test_the_run_is_built_once_as_the_prepare_stage_ends(self):
        # The build is the prepare's last statement before its return; the
        # shell calls the prepare, then the solve.
        self.assertEqual(len(self.builds), 1)
        last = self.prepare.body[-2]
        self.assertEqual(ast.unparse(last.targets[0]), "_run")
        self.assertIs(last.value, self.builds[0])
        self.assertIsInstance(self.prepare.body[-1], ast.Return)
        self.assertLess(self.prepare_call.lineno, self.solve_call.lineno)
        self.assertEqual(sorted(k.arg for k in self.builds[0].keywords),
                         sorted(self.fields))

    def test_every_holder_is_built_from_its_same_named_locals(self):
        # A swapped pair in a 25-keyword constructor passes every other pin
        # (TJ-1943): each keyword's value is the local of that name, or, for
        # the iteration's positions and the loss's pre-existence, an expression.
        t = ast.parse(_engine.source())
        for cls in ("_CanadaRun", "_CanadaIteration", "_SLPass", "_SLLoss", "_SLHeld", "_USRun"):
            for b in [n for n in ast.walk(t) if isinstance(n, ast.Call)
                      and getattr(n.func, "id", None) == cls]:
                for k in b.keywords:
                    v = k.value
                    if isinstance(v, ast.Name):
                        self.assertEqual(v.id, k.arg, cls)
                    elif isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) \
                            and v.value.id in ("it", "run", "pas"):
                        self.assertEqual(v.attr, k.arg, cls)    # the same field, handed on
                    else:
                        # the two values computed in the construction itself
                        self.assertIn(k.arg, ("_pos", "loss_pre"), cls)

    def test_no_field_is_rebound_from_the_build_to_the_solve_call(self):
        # The loop is `_ca_solve` (step 4o), which reads the fields through
        # `run` and binds none of their names; the shell's statements from
        # the build to the call bind none either.
        after = [st for st in self.cg.body
                 if self.prepare_call.lineno <= st.lineno <= self.solve_call.lineno]
        self.assertEqual(sorted(set(self.fields) & _stores(self.solve)), [])
        self.assertEqual(sorted(set(self.fields) & _stores(self.prepare.body[-1])), [])
        stored = set()
        for st in after:
            stored |= _stores(st)
        stored -= {"_run"}
        self.assertEqual(sorted(set(self.fields) & stored), [])

    def test_the_runs_helpers_take_the_run(self):
        # Since step 4r (TJ-1964) the run carries no partial over the engine's
        # locals: its helpers are module functions taking `run` first and
        # reading their inputs from it, and the only callable fields left are
        # inputs from outside the engine (RUN_CALLABLE_INPUTS).
        before = [st for st in self.prepare.body if st.lineno < self.builds[0].lineno]
        partials = [ast.unparse(st.targets[0]) for st in before
                    if isinstance(st, ast.Assign) and isinstance(st.value, ast.Call)
                    and ast.unparse(st.value.func) == "functools.partial"
                    and ast.unparse(st.targets[0]) in self.fields]
        self.assertEqual(partials, [])
        self.assertEqual(sorted(k for k in self.helpers if k in RUN_HELPERS), sorted(RUN_HELPERS))
        for name, fn in self.helpers.items():
            if name not in RUN_HELPERS:
                continue
            self.assertEqual(fn.args.args[0].arg, "run", name)
            self.assertTrue(_reads(fn, "run"), name)      # it reads the run it takes
        t = ast.parse(_engine.source())
        annotated = {st.target.id: ast.unparse(st.annotation)
                     for n in ast.walk(t) if isinstance(n, ast.ClassDef) and n.name == "_CanadaRun"
                     for st in n.body if isinstance(st, ast.AnnAssign)}
        callables = sorted(k for k, v in annotated.items() if v.startswith("Callable"))
        self.assertEqual(callables, sorted(RUN_CALLABLE_INPUTS))

    def test_each_function_reads_its_own_fields(self):
        # The pass's fields are read by the pass or by its four parts and
        # their helpers, which take `run` too (step 4h); the walk's by the
        # walk alone.
        h = self.helpers
        pass_side = sorted(set(_reads_via(self.funcs["_superficial_loss_pass"], "run", h))
                           | {x for k in PARTS + UNIT_HELPERS + ALLOCATION_HELPERS
                              for x in _reads_via(self.parts[k], "run", h)})
        self.assertEqual(pass_side, PASS_FIELDS)
        walk_side = sorted({x for k in ("_walk_rows",) + WALK_PARTS
                            for x in _reads_via(self.funcs[k], "run", h)})
        self.assertEqual(walk_side, WALK_FIELDS)
        self.assertEqual(_reads_via(self.solve, "run", h), SOLVE_FIELDS)
        self.assertEqual(_reads_via(self.finish, "run", h), FINISH_FIELDS)
        self.assertEqual(sorted(set(PASS_FIELDS) | set(WALK_FIELDS) | set(SOLVE_FIELDS)
                                | set(FINISH_FIELDS)), sorted(self.fields))

    def test_the_parts_take_the_objects_and_their_own_name(self):
        want = {"_sl_trigger_scan": ["loss", "pas", "run"],
                "_sl_window_walk": ["pas", "rec", "run"],
                "_sl_window_trace": ["disallowed_qty", "held", "pas", "rec", "run"],
                "_sl_allocate": ["held", "pas", "rec", "run"],
                "_sl_pre_exists": ["run"],
                "_sl_row_loss_units": ["rec", "run"],
                "_sl_call_units": ["pas", "rec", "run"],
                "_sl_avail_native": ["pas", "rec"],
                "_sl_gone_by_end": ["rec"],
                "_sl_trigger_holder": ["pas", "rec", "run"],
                "_sl_pool_rows": ["account", "pas", "rec", "run", "symbol"],
                "_sl_sold_out": ["pas", "rec", "run"],
                "_sl_sale_alloc": ["held", "loss_pkey", "pas", "rec", "run"],
                "_sl_landing_row": ["pas", "rec", "run", "start", "symbol"],
                "_sl_mk_adjust": ["land", "loss_key", "loss_pkey", "pas", "rec", "run"]}
        for k, params in want.items():
            self.assertEqual([a.arg for a in self.parts[k].args.kwonlyargs], params, k)

    def test_the_walk_parts_take_the_row_locals_they_read(self):
        # Each branch's keyword-only inputs are the walk's locals it reads
        # (every one read: an input nothing reads fails here, TJ-1877), and
        # every other name it reads is the module's or a builtin: a walk
        # local read inside a part is a NameError on that path (TJ-1988),
        # flow-sensitively, so a name the part binds on one path and reads
        # on another fails here too (TJ-1994).
        for k, params in WALK_PART_INPUTS.items():
            part = self.funcs[k]
            self.assertEqual([a.arg for a in part.args.kwonlyargs], params, k)
            self.assertEqual(part.args.args, [], k)
            self.assertEqual(sorted(set(params) - _loads(part)), [], k)
            self.assertEqual(sorted(free_names(part) - self.module_names), [], k)

    def test_each_object_field_is_read_by_a_part_and_nothing_else_is(self):
        # `_SLPass`, `_SLLoss` and `_SLHeld`: the fields are exactly what the
        # four parts and their helpers read through `pas`, `rec` and `held` (a
        # field no part reads, or a read of a name that is not a field, fails
        # here).
        for cls, obj in (("_SLPass", "pas"), ("_SLLoss", "rec"), ("_SLHeld", "held")):
            fields = sorted(self.objects[cls])
            read = sorted({x for k in PARTS + UNIT_HELPERS + ALLOCATION_HELPERS
                           for x in _reads_via(self.parts[k], obj, self.helpers)})
            self.assertEqual(read, fields, cls)

    def test_the_pass_holder_carries_facts_and_scratch_not_closures(self):
        # Since step 4r (slice 3) the pass's eight closures are module helpers
        # over `pas` and `run`; no field of the pass holder is a callable.
        t = ast.parse(_engine.source())
        annotated = {st.target.id: ast.unparse(st.annotation)
                     for n in ast.walk(t) if isinstance(n, ast.ClassDef) and n.name == "_SLPass"
                     for st in n.body if isinstance(st, ast.AnnAssign)}
        self.assertEqual([k for k, v in annotated.items() if v.startswith("Callable")], [])
        self.assertEqual(sorted(k for k in self.helpers if k in PASS_HELPERS), sorted(PASS_HELPERS))
        for name in ("_sl_holder", "_sl_holder_rank"):
            self.assertEqual(self.helpers[name].args.args[0].arg, "run", name)
        for name in ("_sl_opening_qty", "_sl_wpos", "_sl_class_balances"):
            self.assertEqual(self.helpers[name].args.args[0].arg, "pas", name)



class TestCanadaIteration(unittest.TestCase):
    """`_CanadaIteration` (step 4i): what the loop binds each iteration for
    the walk, built once per iteration right before the walk's call; its
    fields are exactly what the walk and the denial pass read through `it`,
    and the loop rebinds none of them between the build and the walk."""

    @classmethod
    def setUpClass(cls):
        t = ast.parse(_engine.source())
        cls.fields = [st.target.id for n in ast.walk(t)
                      if isinstance(n, ast.ClassDef) and n.name == "_CanadaIteration"
                      for st in n.body if isinstance(st, ast.AnnAssign)]
        engine = [n for n in t.body if isinstance(n, ast.ClassDef)
                  and n.name == "CanadaTaxRules"][0]
        cg = [n for n in engine.body if isinstance(n, ast.FunctionDef)
              and n.name == "compute_gains"][0]
        cls.cg = cg
        cls.solve = [n for n in t.body if isinstance(n, ast.FunctionDef)
                     and n.name == "_ca_solve"][0]
        cls.loop = [st for st in cls.solve.body if isinstance(st, ast.For)
                    and "iteration" in ast.unparse(st.target)][0]
        cls.builds = [n for n in ast.walk(cls.solve) if isinstance(n, ast.Call)
                      and getattr(n.func, "id", None) == "_CanadaIteration"]
        cls.walk_call = [st for st in cls.loop.body
                         if "_walk_rows(" in ast.unparse(st)][0]
        cls.funcs = {n.name: n for n in ast.walk(t)
                     if isinstance(n, ast.FunctionDef)
                     and n.name in ("_superficial_loss_pass", "_walk_rows") + WALK_PARTS}

    def test_built_once_per_iteration_before_the_walk(self):
        self.assertEqual(len(self.builds), 1)
        b = self.builds[0]
        self.assertTrue(self.loop.lineno < b.lineno < self.walk_call.lineno)
        self.assertEqual(sorted(k.arg for k in b.keywords), sorted(self.fields))

    def test_no_field_is_rebound_between_the_build_and_the_last_reader(self):
        # The pass is the object's last reader in the loop (TJ-1938).
        pass_call = [st for st in self.loop.body
                     if "_superficial_loss_pass(" in ast.unparse(st)][0]
        between = [st for st in self.loop.body
                   if self.builds[0].lineno < st.lineno <= pass_call.lineno]
        stored = set()
        for st in between:
            stored |= _stores(st)
        self.assertEqual(sorted(set(self.fields) & stored), [])

    def test_the_walk_never_mutates_the_row_list(self):
        # The positions are built before the walk (TJ-1939, TJ-1944): the
        # walk may read `it.current_tx_list` but never call a mutator on it
        # or assign into it.
        mutators = ("append", "extend", "insert", "pop", "remove", "sort",
                    "reverse", "clear")
        nodes = [n for k in ("_walk_rows",) + WALK_PARTS for n in ast.walk(self.funcs[k])]
        for n in nodes:
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                v = n.func.value
                if (isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name)
                        and v.value.id == "it" and v.attr == "current_tx_list"):
                    self.assertNotIn(n.func.attr, mutators)
            if isinstance(n, (ast.Assign, ast.AugAssign, ast.Delete)):
                targets = (n.targets if isinstance(n, (ast.Assign, ast.Delete))
                           else [n.target])
                for tgt in targets:
                    self.assertFalse(ast.unparse(tgt).startswith("it.current_tx_list"))

    def test_the_iterations_helpers_take_the_run_and_the_iteration(self):
        # Since step 4r the iteration carries no partial: its helpers are
        # module functions taking `run` then `it` and reading their inputs
        # from them, and no field is a callable.
        partials = [ast.unparse(st.targets[0]) for st in self.loop.body
                    if isinstance(st, ast.Assign) and isinstance(st.value, ast.Call)
                    and ast.unparse(st.value.func) == "functools.partial"
                    and ast.unparse(st.targets[0]) in self.fields]
        self.assertEqual(partials, [])
        t = ast.parse(_engine.source())
        helpers = {n.name: n for n in t.body if isinstance(n, ast.FunctionDef)
                   and n.name in ITERATION_HELPERS}
        self.assertEqual(sorted(helpers), sorted(ITERATION_HELPERS))
        for name, fn in helpers.items():
            self.assertEqual([a.arg for a in fn.args.args[:2]], ["run", "it"], name)
            self.assertTrue(_reads(fn, "it"), name)
        annotated = {st.target.id: ast.unparse(st.annotation)
                     for n in ast.walk(t) if isinstance(n, ast.ClassDef)
                     and n.name == "_CanadaIteration"
                     for st in n.body if isinstance(st, ast.AnnAssign)}
        self.assertEqual([k for k, v in annotated.items() if v.startswith("Callable")], [])

    def test_the_fields_are_what_the_walk_and_the_pass_read(self):
        t = ast.parse(_engine.source())
        helpers = {n.name: n for n in t.body if isinstance(n, ast.FunctionDef)
                   and n.name in RUN_HELPERS}
        read = set(_reads_via(self.funcs["_superficial_loss_pass"], "it", helpers))
        for k in ("_walk_rows",) + WALK_PARTS:
            read |= set(_reads_via(self.funcs[k], "it", helpers))
        self.assertEqual(sorted(read), sorted(self.fields))



class TestUSRun(unittest.TestCase):
    """`_USRun` (step 4l): what the US engine binds before its walk, built
    with every field named as `_prepare` ends (step 4p); its fields are
    exactly what the walk, its parts and `_finish` read through `run`; the
    engine's helpers are partials over locals bound before the partial (the
    pins `_CanadaRun` has, TJ-1976); the parts (step 4n) take the row's locals
    they read; the compensation planner's building run is the stages (4q)."""

    @classmethod
    def setUpClass(cls):
        t = ast.parse(_engine.source())
        cls.fields = [st.target.id for n in ast.walk(t)
                      if isinstance(n, ast.ClassDef) and n.name == "_USRun"
                      for st in n.body if isinstance(st, ast.AnnAssign)]
        cls.cg = [x for x in ast.walk(t) if isinstance(x, ast.FunctionDef)
                  and x.name == "compute_gains"
                  and any(isinstance(n, ast.Call) and getattr(n.func, "id", None)
                          == "_us_walk_rows" for n in ast.walk(x))][0]
        cls.walk = [n for n in ast.walk(t) if isinstance(n, ast.FunctionDef)
                    and n.name == "_us_walk_rows"][0]
        cls.parts = {n.name: n for n in ast.walk(t) if isinstance(n, ast.FunctionDef)
                     and n.name in US_WALK_PARTS}
        cls.module_names = _module_names(t)
        # The shell is three stages (step 4p): `_prepare` builds the run,
        # the walk, `_finish` the report.
        engine = [n for n in t.body if isinstance(n, ast.ClassDef)
                  and n.name == "USATaxRules"][0]
        cls.engine = engine
        cls.prepare = [n for n in engine.body if isinstance(n, ast.FunctionDef)
                       and n.name == "_prepare"][0]
        cls.planner = [n for n in engine.body if isinstance(n, ast.FunctionDef)
                       and n.name == "_plan_paid_compensation"][0]
        cls.finish = [n for n in engine.body if isinstance(n, ast.FunctionDef)
                      and n.name == "_finish"][0]
        cls.builds = [n for n in ast.walk(cls.prepare) if isinstance(n, ast.Call)
                      and getattr(n.func, "id", None) == "_USRun"]
        cls.prepare_call = _call_stmt(cls.cg, "_prepare")
        cls.call = [n for n in ast.walk(cls.cg) if isinstance(n, ast.Call)
                    and getattr(n.func, "id", None) == "_us_walk_rows"][0]
        cls.targets = {n.name: n for n in t.body if isinstance(n, ast.FunctionDef)}

    def test_built_once_as_the_prepare_stage_ends_with_every_field(self):
        self.assertEqual(len(self.builds), 1)
        last = self.prepare.body[-2]
        self.assertEqual(ast.unparse(last.targets[0]), "_run")
        self.assertIs(last.value, self.builds[0])
        self.assertIsInstance(self.prepare.body[-1], ast.Return)
        self.assertLess(self.prepare_call.lineno, self.call.lineno)
        self.assertEqual(sorted(k.arg for k in self.builds[0].keywords),
                         sorted(self.fields))

    def test_the_planner_runs_the_stages_with_compute_gains_defaults(self):
        # The compensation planner's building run is the prepare, the walk
        # and the payments plan, in that order inside the silent run (step
        # 4q; the plan after the silent run): its `kwargs` names every keyword the prepare takes, the books
        # and the plan apart; a setting the booking run hands the planner is
        # the planner's own parameter, whose default is `compute_gains`'s;
        # the rest are literals equal to those defaults; the call is checked
        # against the prepare's signature before the silent run (TJ-2046,
        # TJ-2047, TJ-2048, TJ-2050).
        defaults = inspect.signature(USATaxRules.compute_gains).parameters
        want = {a.arg for a in self.prepare.args.kwonlyargs}
        kw = [st for st in self.planner.body if isinstance(st, ast.Assign)
              and ast.unparse(st.targets[0]) == "kwargs"][0].value
        self.assertEqual(ast.unparse(kw.func), "dict")
        self.assertEqual(kw.args, [])
        named = {k.arg: k.value for k in kw.keywords}
        self.assertEqual(sorted(named), sorted(want))
        own = {a.arg: d for a, d in zip(self.planner.args.kwonlyargs,
                                        self.planner.args.kw_defaults)}
        handed = [n for n in ast.walk(self.prepare) if isinstance(n, ast.Call)
                  and getattr(n.func, "attr", None) == "_plan_paid_compensation"][0]
        self.assertEqual(sorted(k.arg for k in handed.keywords), sorted(own))
        for k in handed.keywords:
            self.assertEqual(ast.unparse(k.value), k.arg)
        for name, value in named.items():
            if name in ("transactions", "sheltered_transactions",
                        "affiliated_transactions", "_paid_comp"):
                continue
            if name in own:
                self.assertEqual(ast.unparse(value), name)
                self.assertEqual(ast.literal_eval(own[name]), defaults[name].default, name)
            else:
                self.assertEqual(ast.literal_eval(value), defaults[name].default, name)
        bind = [st for st in self.planner.body if isinstance(st, ast.Expr)
                and isinstance(st.value, ast.Call)
                and getattr(st.value.func, "attr", None) == "bind"]
        self.assertEqual(len(bind), 1)
        tries = [st for st in self.planner.body if isinstance(st, ast.Try)]
        self.assertEqual(len(tries), 1)
        self.assertLess(bind[0].lineno, tries[0].lineno)
        with_ = tries[0].body[0]
        self.assertIsInstance(with_, ast.With)
        def name(st):
            return getattr(st.value.func, "attr", None) or getattr(st.value.func, "id", None)
        calls = [(name(st), st) for st in with_.body
                 if isinstance(getattr(st, "value", None), ast.Call)]
        self.assertEqual([c for c, _ in calls], ["_prepare", "_us_walk_rows"])
        self.assertEqual(ast.unparse(calls[0][1].targets[0]), "run")
        # The plan itself follows the silent run: an error in it is raised,
        # not planned away (TJ-2056).
        after = self.planner.body[self.planner.body.index(tries[0]) + 1]
        self.assertIsInstance(getattr(after, "value", None), ast.Call)
        self.assertEqual(name(after), "_us_plan_payments")
        for st in (calls[1][1], after):
            self.assertEqual([(k.arg, ast.unparse(k.value)) for k in st.value.keywords],
                             [("run", "run")])

    def test_the_fields_are_what_the_walk_and_the_finish_read(self):
        # ... the walk and its parts (step 4n), which take `run` too, the
        # finish stage (step 4p) and the payments plan out of it (4q).
        h = {k: self.targets[k] for k in US_RUN_HELPERS}
        read = set(_reads_via(self.walk, "run", h)) | set(_reads_via(self.finish, "run", h))
        for k in ("_us_plan_payments", "_us_dividend_entries"):
            read |= set(_reads_via(self.targets[k], "run", h))
        for k in US_WALK_PARTS:
            read |= set(_reads_via(self.parts[k], "run", h))
        self.assertEqual(sorted(read), sorted(self.fields))

    def test_the_walk_parts_take_the_row_locals_they_read(self):
        # Each block's keyword-only inputs are the walk's locals it reads,
        # every one read, and its other reads are the module's, on every
        # path (the Canadian walk's pin, step 4m, TJ-1988, TJ-1994).
        for k, params in US_WALK_PART_INPUTS.items():
            part = self.parts[k]
            self.assertEqual([a.arg for a in part.args.kwonlyargs], params, k)
            self.assertEqual(part.args.args, [], k)
            self.assertEqual(sorted(set(params) - _loads(part)), [], k)
            self.assertEqual(sorted(free_names(part) - self.module_names), [], k)

    def test_no_field_is_rebound_from_the_prepare_to_the_walk(self):
        # The build is the prepare's last statement before its return (the
        # test above); the shell's statements from the prepare's call to
        # the walk's rebind no field's name.
        between = [st for st in self.cg.body
                   if self.prepare_call.lineno < st.lineno <= self.call.lineno]
        stored = set()
        for st in between:
            stored |= _stores(st)
        stored -= {"_run"}
        self.assertEqual(sorted(set(self.fields) & stored), [])

    def test_the_helpers_take_the_run_and_the_prepass_keeps_its_partials(self):
        # Since step 4r (TJ-1983) the run carries no partial: the engine's helpers
        # are module functions taking `run` first and reading their inputs from
        # it; three the pre-run scan and recorder share stay pure over one
        # keyword input the walk hands from the run; the prepare's remaining
        # partials are the pre-run consumers' alone, none of them a run field;
        # the run's callable fields are inputs from outside the engine.
        partials = [st.targets[0].id for st in self.prepare.body
                    if isinstance(st, ast.Assign) and len(st.targets) == 1
                    and isinstance(st.targets[0], ast.Name) and isinstance(st.value, ast.Call)
                    and ast.unparse(st.value.func) == "functools.partial"]
        self.assertEqual(sorted(partials), sorted(US_PREPASS_PARTIALS))
        self.assertEqual(sorted(set(partials) & set(self.fields)), [])
        for name in US_RUN_HELPERS:
            fn = self.targets[name]
            self.assertEqual(fn.args.args[0].arg, "run", name)
            # it reads the run, or hands it to a helper that does
            self.assertTrue(any(isinstance(n, ast.Name) and n.id == "run"
                                and isinstance(n.ctx, ast.Load) for n in ast.walk(fn)), name)
        for name in US_PURE_HELPERS:
            fn = self.targets[name]
            self.assertNotEqual(fn.args.args[0].arg, "run", name)
            self.assertEqual(len(fn.args.kwonlyargs), 1, name)
        t = ast.parse(_engine.source())
        annotated = {st.target.id: ast.unparse(st.annotation)
                     for n in ast.walk(t) if isinstance(n, ast.ClassDef) and n.name == "_USRun"
                     for st in n.body if isinstance(st, ast.AnnAssign)}
        self.assertEqual(sorted(k for k, v in annotated.items() if v.startswith("Callable")),
                         sorted(US_RUN_CALLABLE_INPUTS))

    def test_the_finish_stages_partials_bind_the_runs_fields_or_locals(self):
        # The finish stage's partials (step 4p) are the report's entries' and
        # the payments plan's since 4q: each keyword the target's, its value
        # the run's same-named field or a local bound before (TJ-2038).
        stages = [self.finish, self.targets["_us_dividend_entries"],
                  self.targets["_us_plan_payments"]]
        found = 0
        for stage in stages:
          bound = {a.arg for a in stage.args.args + stage.args.kwonlyargs}
          for st in stage.body:
              if isinstance(st, ast.Assign) and len(st.targets) == 1 \
                      and isinstance(st.targets[0], ast.Name) \
                      and isinstance(st.value, ast.Call) \
                      and ast.unparse(st.value.func) == "functools.partial":
                  name = st.targets[0].id
                  target = self.targets[st.value.args[0].id]
                  self.assertTrue({k.arg for k in st.value.keywords}
                                  <= {a.arg for a in target.args.kwonlyargs}, name)
                  for k in st.value.keywords:
                      v = k.value
                      if isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) \
                              and v.value.id == "run":
                          self.assertEqual(v.attr, k.arg, name)
                      else:
                          self.assertIsInstance(v, ast.Name, name)
                          self.assertEqual(v.id, k.arg, name)
                          self.assertIn(k.arg, bound, name)
                  found += 1
              bound |= _stores(st)
        self.assertEqual(found, 7)     # the report's two, the entries' one, the plan's four


def _fold_check(src: str):
    """TJ-2683: (the mutable `_CanadaIteration` fields `_WalkFold.save` does not
    read, the run fields the walk writes that it does not read), from the module's
    source. `save` reads a field as `it.<name>` (or through `_lists(it)`), a run
    field as `self._run.<name>`."""
    t = ast.parse(src)
    classes = {n.name: n for n in t.body if isinstance(n, ast.ClassDef)}
    fields = [st.target.id for st in classes["_CanadaIteration"].body
              if isinstance(st, ast.AnnAssign)]
    fold = {m.name: m for m in classes["_WalkFold"].body if isinstance(m, ast.FunctionDef)}
    saved_it = set(_reads(fold["save"], "it")) | set(_reads(fold["_lists"], "it"))
    saved_run = {n.attr for n in ast.walk(fold["save"]) if isinstance(n, ast.Attribute)
                 and isinstance(n.value, ast.Attribute) and n.value.attr == "_run"
                 and isinstance(n.value.value, ast.Name) and n.value.value.id == "self"}
    funcs = {n.name: n for n in t.body if isinstance(n, ast.FunctionDef)
             and n.name in ("_walk_rows",) + WALK_PARTS + RUN_HELPERS}
    mutators = ("add", "append", "extend", "insert", "pop", "remove", "clear",
                "update", "setdefault", "discard", "sort")
    written = set()
    for fn in funcs.values():
        for n in ast.walk(fn):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in mutators):
                v = n.func.value
                if isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) and v.value.id == "run":
                    written.add(v.attr)
            if isinstance(n, (ast.Assign, ast.AugAssign)):
                for tgt in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                    base = tgt.value if isinstance(tgt, ast.Subscript) else tgt
                    if (isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name)
                            and base.value.id == "run"):
                        written.add(base.attr)
    return (set(fields) - WALK_FOLD_EXEMPT_IT - saved_it,
            written - WALK_FOLD_EXEMPT_RUN - saved_run, saved_it, written)


# What `save` need not keep, and why (TJ-2683).
WALK_FOLD_EXEMPT_IT = {
    "current_tx_list",      # never mutated (test_the_walk_never_mutates_the_row_list)
    "_pos", "_dis_by_id", "_jlegs", "_moved_legs",   # built per iteration, read only
    "iteration",            # an int
}
WALK_FOLD_EXEMPT_RUN = {"_ev_memo"}    # a memo of a pure function of the row


class TestWalkFold(unittest.TestCase):
    """`_WalkFold.save` names the walk's mutable state by hand (TJ-2683): every
    mutable `_CanadaIteration` field, and every run field the walk writes, is read
    in `save`, or is on the exempt list above with its reason; a name dropped from
    `save` fails here."""

    def test_every_mutable_field_and_written_run_field_is_saved(self):
        missing_it, missing_run, saved_it, written = _fold_check(_engine.source())
        self.assertEqual(sorted(missing_it), [])
        self.assertEqual(sorted(missing_run), [])
        self.assertFalse(saved_it & WALK_FOLD_EXEMPT_IT)       # the exempt list stays honest
        self.assertEqual(sorted(written), ["_ev_memo", "_lot_seq", "_short_routed"])

    def test_a_name_dropped_from_save_is_reported(self):
        src = _engine.source()
        self.assertEqual(src.count("set(it._jdone)"), 1)
        self.assertEqual(_fold_check(src.replace("set(it._jdone)", "set()", 1))[0], {"_jdone"})
        self.assertEqual(_fold_check(src.replace("self._run._short_routed", "self._run._x", 1))[1],
                         {"_short_routed"})


if __name__ == "__main__":
    unittest.main()
