#!/usr/bin/env python3
"""Targeted mutation testing for the taxjson gains engine.

Generates one-line mutants (comparison/arithmetic/boolean/constant
tweaks) inside the wash-relevant regions of the gains engine (the engine
package's modules, src/taxjson/engine/) and lib/pipeline.py,
runs the fast engine test set against each, and reports SURVIVORS —
mutants the tests fail to kill. A survivor is either an equivalent
mutant (no observable behavior change) or a genuine test gap.

`--parser ID` mutates one parser instead (PARSER_TARGETS, PLAN step 16): a
sample of its sites stratified by function (`--sample N --seed S`, printed
so the run can be repeated), against the sets a line trace of every test
module gives (`load_trace`, `parser_sets`): A, the quick ones, and B, the
slower ones, each cut per mutant to the modules that ran the mutated line
(`cut_to_line`; a line run at import is run by every module that ran the
file); C, the golden ledgers and views, whole. A kill stops at the first
failing module, and counts once that module fails again alone with the
mutant and passes alone with the file unmutated; one that does not (a
flaky module, or one that fails only after others) is set aside for that
mutant, named, and the judgement goes on without it. A mutant that passes
them is confirmed before it is called a survivor: the rest of A, the
modules outside its cut, run with it applied; a kill there is "killed by A
(outside its cut)", counted apart as a cut miss (`cut_misses`: each is a
bug of the trace), and a run with one runs its survivors against the rest
of B too. A survivor whose line a module left out of the run reaches (it
failed in the trace or the control) is flagged. The trace is an
instrument in the targets' own files (`instrument_source`), so a process
a test starts with an environment of its own is traced too; one that
cannot tell its test module is counted (`TRACE_LOSSES`), never lost
silently. Before the mutants, the control (`control_run`): every module of
the sets, run once with the targets rewritten as each mutant is but
unmutated; one that fails is set aside, and named in the report and the
record.

The trace decides the work, never the verdict: a kill rests on the
control and on its own set passing unmutated, a survivor on whole A, whole
C and B cut by the trace (B alone trusts the trace, and only until a run
finds a cut miss). A run is recorded only from a tree that is its
commit's, files git ignores included (`record_commit`; `--no-record` runs
one that is not, as a proof), and names the commit's tree, which a
rebase-merge keeps: record a run from a landed commit. Each run's counts
go in scripts/mutation-runs.json.

Mutates IN PLACE with backup/restore. The original source is written
back after every mutant, and on Ctrl-C, SIGTERM, SIGHUP (a killed
`timeout`, an ssh drop, a CI cancel) or any other exit Python gets to
run; only SIGKILL or a power cut can leave a mutant applied — restore
with `git checkout -- src/taxjson/`. Writes progress + results to
mutation_report.txt next to this script.
"""
import ast
import contextlib
import hashlib
import json
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import (Any, Callable, Dict, Iterable, List, NamedTuple, Optional, Sequence, Set,
                    Tuple)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(REPO, "venv/bin/python3")
HERE = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(HERE, "..", "mutation_report.txt")

# The regions to mutate, by FUNCTION NAME (audit S025-02: hand-entered
# line ranges went stale — the "US wash replacement" range had drifted
# into the Canadian engine, and the superficial-loss window and the
# whole US engine were never mutated). Resolved to line ranges with the
# AST at start-up; a name that no longer exists stops the harness.
TARGET_FUNCS = [
    # Canada: the fixed-point loop's shell, its walk over the rows (ACB
    # pools, ADJUST application) and its superficial-loss denial pass
    # (the window, the solver), moved out of compute_gains by steps 4a
    # and 4b of the engine refactor.
    # The stages both engines share (step 4f).
    ("src/taxjson/engine/taxrules.py", "TaxRules._dedupe_splits"),
    ("src/taxjson/engine/taxrules.py", "TaxRules._emit_replacement_warnings"),
    ("src/taxjson/engine/taxrules.py", "TaxRules._stamp_overrides"),
    ("src/taxjson/engine/ca/taxrules.py", "CanadaTaxRules._prepare"),
    ("src/taxjson/engine/ca/taxrules.py", "CanadaTaxRules._finish"),
    ("src/taxjson/engine/ca/solve.py", "_ca_solve"),
    ("src/taxjson/engine/ca/walk.py", "_walk_rows"),
    ("src/taxjson/engine/ca/walk.py", "_walk_adjust"),
    ("src/taxjson/engine/ca/walk.py", "_walk_split"),
    ("src/taxjson/engine/ca/walk.py", "_walk_trade"),
    ("src/taxjson/engine/ca/denial.py", "_superficial_loss_pass"),
    ("src/taxjson/engine/ca/denial.py", "_sl_trigger_scan"),
    ("src/taxjson/engine/ca/denial.py", "_sl_window_walk"),
    ("src/taxjson/engine/ca/denial.py", "_sl_allocate"),
    ("src/taxjson/engine/ca/denial.py", "_sl_window_trace"),
    ("src/taxjson/engine/ca/run_helpers.py", "_sl_holder"),
    ("src/taxjson/engine/ca/run_helpers.py", "_sl_holder_rank"),
    ("src/taxjson/engine/ca/run_helpers.py", "_sl_pkey"),
    ("src/taxjson/engine/ca/run_helpers.py", "_sl_call_und"),
    ("src/taxjson/engine/ca/run_helpers.py", "_sl_symbol_asof"),
    ("src/taxjson/engine/ca/run_helpers.py", "_sl_opening_qty"),
    ("src/taxjson/engine/ca/run_helpers.py", "_sl_wpos"),
    ("src/taxjson/engine/ca/denial.py", "_sl_class_balances"),
    ("src/taxjson/engine/ca/indexes.py", "_own_move_held"),
    # The pool's operations outside the walk's four functions (PLAN step
    # 13, commit 0: no mutant of them was killed before their pins, the
    # test_pool_* modules of tests/rules/ca/{acb,corp,opt,rpt,sl,xlist}/ and
    # rules.ca.opt.test_written_contracts_per_account).
    ("src/taxjson/engine/ca/own_moves.py", "_own_move_units"),
    ("src/taxjson/engine/ca/own_moves.py", "_own_move_written"),
    ("src/taxjson/engine/ca/own_moves.py", "_journal_units"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.taint"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.taint_from"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.drain"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.untaint"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.add_cost"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.park"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.tally_denied"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.give_up"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.cut_for_roc"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.restore_roc"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.reset_negative"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.late_premium"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.move_in"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.move_beyond"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.move_out"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.move_out_lots"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.move_in_from"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.leftover"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.split"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.merge"),
    ("src/taxjson/engine/ca/walk.py", "_split_short_pool"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.open"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.take_pending"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.add_wash_cut"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.record_grant"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.record_grant_loss"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.snapshot"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.part"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.netted"),
    ("src/taxjson/engine/ca/pool.py", "PoolBook.give_up_to_child"),
    ("src/taxjson/engine/ca/pool.py", "PoolBook.child_opens"),
    ("src/taxjson/engine/ca/pool.py", "PoolBook.save"),
    ("src/taxjson/engine/ca/pool.py", "PoolBook.restore"),
    # US: the engine's shell and its walk (FIFO lots and §1091
    # replacement matching), moved out by step 4c.
    ("src/taxjson/engine/us/taxrules.py", "USATaxRules._prepare"),
    ("src/taxjson/engine/us/prepare.py", "_us_scan_events"),
    ("src/taxjson/engine/us/prepare.py", "_us_symbol_currencies"),
    ("src/taxjson/engine/us/prepare.py", "_us_disposition_groups"),
    ("src/taxjson/engine/us/taxrules.py", "USATaxRules._finish"),
    ("src/taxjson/engine/us/prepare.py", "_us_plan_payments"),
    ("src/taxjson/engine/us/prepare.py", "_us_dividend_entries"),
    ("src/taxjson/engine/us/walk.py", "_us_walk_rows"),
    ("src/taxjson/engine/us/walk.py", "_us_walk_lot_move"),
    ("src/taxjson/engine/us/walk.py", "_us_walk_split"),
    ("src/taxjson/engine/us/walk.py", "_us_walk_adjust"),
    ("src/taxjson/engine/us/walk.py", "_us_walk_stock_dividend"),
    ("src/taxjson/engine/us/walk.py", "_us_walk_spinoff"),
    ("src/taxjson/engine/us/walk.py", "_us_walk_reorg"),
    ("src/taxjson/engine/us/trades.py", "_us_walk_buy"),
    ("src/taxjson/engine/us/trades.py", "_us_walk_sell"),
    ("src/taxjson/engine/us/trades.py", "_us_gain_entry"),
    # The engines' helpers as module functions (steps 4k and 4l): every one,
    # so a renamed helper stops the harness here rather than leaving the set.
    ("src/taxjson/engine/ca/run_helpers.py", "_ca_ev_key"),
    ("src/taxjson/engine/ca/run_helpers.py", "_ca_grant_applies"),
    ("src/taxjson/engine/ca/run_helpers.py", "_ca_open_short_option"),
    ("src/taxjson/engine/ca/run_helpers.py", "_ca_opt_root"),
    ("src/taxjson/engine/ca/run_helpers.py", "_opt_root_in"),
    ("src/taxjson/engine/ca/pools.py", "_ca_pool_state"),
    ("src/taxjson/engine/ca/run_helpers.py", "_ca_row_pool_key"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.close_lots"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.open_lot"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.retag_lots"),
    ("src/taxjson/engine/ca/pool.py", "AcbPool.close"),
    ("src/taxjson/engine/ca/run_helpers.py", "_ca_short_lot_open"),
    ("src/taxjson/engine/ca/pools.py", "_ca_short_state"),
    ("src/taxjson/engine/ca/run_helpers.py", "_ca_sort_date"),
    ("src/taxjson/engine/ca/run_helpers.py", "_ca_stock_short_key"),
    ("src/taxjson/engine/ca/run_helpers.py", "_ca_tl_after"),
    ("src/taxjson/engine/ca/pools.py", "_ca_touched"),
    ("src/taxjson/engine/ca/run_helpers.py", "_ca_trade_money"),
    ("src/taxjson/engine/ca/run_helpers.py", "_ca_walk_date"),
    ("src/taxjson/engine/ca/run_helpers.py", "_ca_write_year"),
    ("src/taxjson/engine/us/wash.py", "_us_allot_sold_shares"),
    ("src/taxjson/engine/us/wash.py", "_us_apply_other_stkdivs"),
    ("src/taxjson/engine/us/wash.py", "_us_apply_sold_replacement"),
    ("src/taxjson/engine/us/walk.py", "_us_book_journal"),
    ("src/taxjson/engine/us/walk.py", "_us_carry_lots"),
    ("src/taxjson/engine/us/lots.py", "_us_draw_long_lots"),
    ("src/taxjson/engine/us/wash.py", "_us_dust_noise"),
    ("src/taxjson/engine/us/wash.py", "_us_find_replacements_in_window"),
    ("src/taxjson/engine/us/wash.py", "_us_flag_futures_loss"),
    ("src/taxjson/engine/us/wash.py", "_us_get_sort_date"),
    ("src/taxjson/engine/us/wash.py", "_us_grow_rep_capacity"),
    ("src/taxjson/engine/us/lot_store.py", "_us_ikey"),
    ("src/taxjson/engine/us/lot_store.py", "_us_ikey_on"),
    ("src/taxjson/engine/us/lot_store.py", "_us_insert_lots"),
    ("src/taxjson/engine/us/lot_store.py", "_us_inv_keys_for"),
    ("src/taxjson/engine/us/prepare.py", "_us_inventory_rows"),
    ("src/taxjson/engine/us/wash.py", "_us_ix"),
    ("src/taxjson/engine/us/prepare.py", "_us_lot_rows"),
    ("src/taxjson/engine/us/lot_store.py", "_us_lots_copy"),
    ("src/taxjson/engine/us/lots.py", "_us_move_long_lots"),
    ("src/taxjson/engine/us/wash.py", "_us_nkey"),
    ("src/taxjson/engine/us/wash.py", "_us_non_capital"),
    ("src/taxjson/engine/us/wash.py", "_us_note"),
    ("src/taxjson/engine/us/prepare.py", "_us_open_qty"),
    ("src/taxjson/engine/us/wash.py", "_us_outside_1091"),
    ("src/taxjson/engine/us/lot_store.py", "_us_ov_loss"),
    ("src/taxjson/engine/us/lot_store.py", "_us_ov_take"),
    ("src/taxjson/engine/us/prepare.py", "_us_paid_pil_treatment"),
    ("src/taxjson/engine/us/lots.py", "_us_plan_disposition"),
    ("src/taxjson/engine/us/lots.py", "_us_plan_draw"),
    ("src/taxjson/engine/us/prepare.py", "_us_plan_paid_pil"),
    ("src/taxjson/engine/us/lot_store.py", "_us_real_stamp"),
    ("src/taxjson/engine/us/wash.py", "_us_record_sold_replacement"),
    ("src/taxjson/engine/us/wash.py", "_us_rep_key"),
    ("src/taxjson/engine/us/wash.py", "_us_rkey_on"),
    ("src/taxjson/engine/us/wash.py", "_us_rep_units_factor"),
    ("src/taxjson/engine/us/lots.py", "_us_row_after"),
    ("src/taxjson/engine/us/wash.py", "_us_set_ref"),
    ("src/taxjson/engine/us/wash.py", "_us_sold_replacements_in_window"),
    ("src/taxjson/engine/us/wash.py", "_us_split_covers"),
    ("src/taxjson/engine/us/wash.py", "_us_split_gain_entry"),
    ("src/taxjson/engine/us/wash.py", "_us_split_rep_refs"),
    ("src/taxjson/engine/us/lot_store.py", "_us_take_option_adj"),
    ("src/taxjson/engine/us/lots.py", "_us_tl_held"),
    ("src/taxjson/engine/us/lots.py", "_us_tl_label"),
    ("src/taxjson/engine/us/lots.py", "_us_tl_state"),
    # The assignment pairing and placement, and the forward denial pass's
    # checkpoint (recommendation 10, TJ-1896): a wrong edit there moves a
    # figure with no error.
    ("src/taxjson/engine/assign.py", "_pair_assign_legs"),
    ("src/taxjson/engine/assign.py", "_place_assign_options"),
    ("src/taxjson/engine/assign.py", "_assign_trade_rows"),
    ("src/taxjson/engine/ca/solve.py", "_assign_moved_legs"),
    ("src/taxjson/engine/ca/fold.py", "_fold_same"),
    ("src/taxjson/engine/ca/fold.py", "_WalkFold._fp"),
    ("src/taxjson/engine/ca/fold.py", "_WalkFold._memo"),
    ("src/taxjson/engine/ca/fold.py", "_WalkFold._records"),
    ("src/taxjson/engine/ca/fold.py", "_WalkFold._cut"),
    ("src/taxjson/engine/ca/fold.py", "_WalkFold.restore"),
    ("src/taxjson/engine/ca/fold.py", "_WalkFold.save"),
    ("src/taxjson/engine/ca/fold.py", "_WalkFold._install"),
    # Transfer pre-processing.
    ("src/taxjson/lib/pipeline.py", "_drop_self_cancelling_transfers"),
    ("src/taxjson/lib/pipeline.py", "_net_cross_account_transfers"),
    ("src/taxjson/lib/pipeline.py", "_handle_transfers"),
]


def resolve_targets(funcs=TARGET_FUNCS):
    """[(abs path, first line, last line)] for each (path, qualname)."""
    out = []
    for rel, qual in funcs:
        path = os.path.join(REPO, rel)
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        node, found = tree, None
        for part in qual.split("."):
            found = next((n for n in ast.iter_child_nodes(node)
                          if isinstance(n, (ast.FunctionDef,
                                            ast.AsyncFunctionDef,
                                            ast.ClassDef))
                          and n.name == part), None)
            if found is None:
                raise SystemExit(f"mutation_audit: {qual} not found in "
                                 f"{rel} — update TARGET_FUNCS")
            node = found
        out.append((path, found.lineno, found.end_lineno))
    return out


TARGETS = resolve_targets()

# The parsers (PLAN step 16), by registry id (taxjson.parsers.registry SPECS:
# tests/features/repo_checks/test_mutation_parser_targets.py holds the ids and
# the modules to it): the files every site of which may be mutated.
# lib/cash_readers/ was a target of its own until step 16 commits 7 to 11
# moved every reader into its parser: no reader is left, so no target. `--parser ID` runs one: a sample of its
# sites stratified by function (`stratified_sample`), against the sets the
# trace gives (`parser_sets`), set B cut per mutant to the modules that ran
# the mutated line (`cut_to_line`).
_LB = "src/taxjson/lib/brokerages/"
PARSER_TARGETS: Dict[str, Tuple[str, ...]] = {
    "ib": (_LB + "ib_extractor.py",),
    "questrade": (_LB + "questrade.py",),
    "webull": (_LB + "webull.py",),
    "rbc_direct": (_LB + "rbc_direct.py",),
    "coinbase": (_LB + "coinbase.py",),
    "kraken": (_LB + "kraken.py",),
    "generic": (_LB + "generic.py",),
    # (The cash readers moved into rbc_direct.py, coinbase.py, questrade.py,
    # ib_extractor.py and kraken.py: step 16 commits 7 to 11.)
}
# Every target's files: the one trace records them all (`load_trace`).
TRACED_FILES = tuple(sorted({rel for rels in PARSER_TARGETS.values() for rel in rels}))
# The golden ledgers and views: set C, run whole.
GOLDEN_MODULES = ("test_golden_ledgers", "test_golden_views")
# Set A: the traced modules that ran the target in this many seconds or fewer
# (the cheap killers, run whole); set B the slower ones (cut per mutant).
A_SECONDS = 3.0
# One traced module's limit.
TRACE_TIMEOUT = 900

# The test sets a mutant runs, in order: a mutant is killed by the first
# that fails and runs no later one. First the suite's cheapest killers (about
# 4 s): the modules that killed the most survivors per second when TJ-1896's
# triage reran them against the tests that execute each mutated line (the
# branch-coverage pins killed 249 alone), and the triage's own pins. Then the
# engine's fast set, then the lock the plan names, the golden ledgers (every
# project and engine book, about 25 s) and the solver's equivalence tests: the
# harness ran neither, so a mutant only a golden book catches survived.
TEST_CMDS = [
    [PY, "-m", "unittest", "-q",
     # PLAN step 13, commit 0: the pool operations' pins.
     "rules.ca.acb.test_pool_crossing_zero_and_merged_dates",
     "rules.ca.acb.test_pool_own_move_units_deferral_and_taint",
     "rules.ca.acb.test_pool_residue_after_an_own_move",
     "rules.ca.corp.test_pool_closed_holding_spinoff",
     "rules.ca.corp.test_pool_spinoff_carries_denied_loss",
     "rules.ca.opt.test_pool_written_lots_after_rename_and_drain",
     "rules.ca.rpt.test_pool_cut_deferral_and_late_premium",
     "rules.ca.sl.test_pool_parked_addition_lands_once",
     "rules.ca.sl.test_wash_placement_follows_its_addition",
     "rules.ca.opt.test_premium_ledger_take_returns_its_facts",
     "rules.ca.sl.test_pool_parked_addition_joins_units_that_arrive",
     "rules.ca.xlist.test_pool_listing_journal_carries_the_pool",
     "rules.ca.xlist.test_pool_residue_after_a_journal",
     "rules.ca.sl.test_denial_bump_branches", "rules.us.wash.test_replacement_capacity_and_stock_dividends",
     "rules.ca.acb.test_unknown_cost_option_exercised_or_assigned.TestUnknownCostOptionExercise",
     "rules.us.corp.test_301_cash_fraction_and_355_lots_before_the_record_date.TestTaxableSpinoffFraction",
     "rules.us.corp.test_301_cash_fraction_and_355_lots_before_the_record_date.TestUs355EntitledLots",
     "rules.ca.fx.test_futures_positions_per_broker_account_in_one_folder.TestFuturesBrokerAccountsInOneFolder",
     "rules.ca.sl.test_written_warrant_cover_is_no_replacement.TestWarrantShortCoverIsNoAcquisition",
     "rules.us.date.test_short_cover_loss_dated_by_delivery.TestUsShortLossDeliveredInJanuary",
     "rules.ca.opt.test_two_delivery_legs_share_the_warrants_cost.TestUnsizedWarrantExerciseInParts",
     "rules.us.roc.test_roc_excess_across_small_lots.TestUsRocExcessAcrossSmallLots",
     "rules.ca.sl.test_assignment_ownership_shorts_and_futures_across_accounts.TestCallExpiringOnDay30",
     "rules.ca.sl.test_assignment_ownership_shorts_and_futures_across_accounts.TestCallSplitLineage",
     "rules.ca.sl.test_assignment_ownership_shorts_and_futures_across_accounts.TestFuturesLongShortOwnership",
     "rules.ca.sl.test_assignment_ownership_shorts_and_futures_across_accounts.TestFuturesLongShortTimeline",
     "rules.ca.sl.test_assignment_ownership_shorts_and_futures_across_accounts.TestShortSaleInAnotherAccount",
     "rules.ca.sl.test_assignment_ownership_shorts_and_futures_across_accounts.TestAssignmentRetractsItsOwnAccountsGrant",
     "rules.ca.sl.test_assignment_ownership_shorts_and_futures_across_accounts.TestOneDeliveryForTwoAssignments",
     "rules.ca.sl.test_assignment_ownership_shorts_and_futures_across_accounts.TestJanuaryOptionCloseForDecemberDelivery",
     "test_mutation_pins", "rules.ca.sl.test_deferral_in_merged_pool",
     "rules.us.wash.test_indexed_replacement_scan_figures.TestUsWashScanPinned", "features.engines.test_scan_scaling_and_comment_stripping.TestUsWashScanScales",
     "features.planning.test_radar_capacity_window_scaling.TestRadarCapacityWindow", "features.engines.test_scan_scaling_and_comment_stripping.TestStripJsonComments",
     "features.corporate_actions.test_ib_corporate_action_cancellations_and_deliveries",
     "test_us_option_assignment_crosszero",
     "test_us_corp_actions", "rules.us.corp.test_merger_per_account_ratios.TestF1USMergerPerAccountRatios",
     "rules.ca.crypto.test_micro_coin_denied_loss.TestF2CanadaMicroCryptoDeniedLoss",
     "rules.us.stkdiv.test_stock_dividend_entitled_lots.TestF3USStockDividendEntitledLots",
     "rules.ca.rpt.test_t1135_merger_ratios_and_micro_coins.TestF4T1135MergerRatios",
     "rules.ca.rpt.test_t1135_merger_ratios_and_micro_coins.TestF5T1135MicroCrypto",
     "rules.ca.opt.test_assign_premium_pairing_ledger.TestAssignPremiumPairingLedger",
     "rules.ca.sl.test_same_moment_holder_rank_and_partly_held_option.TestSameMomentHolderRank",
     "rules.ca.sl.test_same_moment_holder_rank_and_partly_held_option.TestCanadaSharesNeverReplaceAPartlyHeldOption",
     "rules.us.basis.test_phantom_lot_loss_outside_1091.TestUsTaintedLongLotNeverFeeds1091",
     "rules.us.wash.test_shares_never_replace_an_option.TestUsSharesNeverReplaceAnOption",
     "rules.us.roc.test_deemed_gain_on_an_old_lot.TestUsRocExcessTerm",
     "rules.ca.crypto.test_quantity_zero_tolerances.TestCanadaQuantityTolerances",
     "rules.ca.acb.test_fee_share_of_a_closing_record.TestCanadaFeeApportionmentEndToEnd",
     "rules.ca.sl.test_bump_timing_and_day_thirty_balances.TestPreLossTriggerBumpTime",
     "rules.us.hold.test_month_end_holding_period.TestUsDecemberMonthEndHoldingPeriod",
     "features.engines.test_gains_json_decimals_option_units_and_split_timestamp.TestGainsJsonRounding",
     "rules.ca.fx.test_trade_converted_at_its_settle_date_rate.TestConvertCurrencySettleDateKey",
     "features.gains_run.test_fee_totals_and_per_account_fee_maps.TestPipelineFeeTotals", "features.gains_run.test_fee_totals_and_per_account_fee_maps.TestSplitGainsFeeMap",
     "features.gains_run.test_fee_totals_and_per_account_fee_maps.TestSplitGainsBlendedInventoryApportioned",
     "rules.ca.opt.test_grant_since_on_the_settle_year.TestGrantSinceUsesTheWritesSettleYear",
     "rules.ca.disp.test_schedule3_acquisition_year_from_days_held.TestDaysHeldDrivesScheduleThreeAcqYear",
     "rules.ca.sl.test_bump_timing_and_day_thirty_balances.TestWashWindowBalanceAtDayThirty",
     "rules.ca.sl.test_bump_timing_and_day_thirty_balances.TestRenameOnWindowEndPoolBacking",
     "features.engines.test_gains_json_decimals_option_units_and_split_timestamp.TestCanadaOptionRecordUnits",
     "features.reports.test_holdings_toml_inventory_currency.TestInventoryCurrencyReachesHoldingsToml",
     "rules.us.wash.test_same_date_replacements_by_clock_time.TestUsReplacementOrderAcquiredByTime",
     "rules.us.basis.test_commission_split_across_fifo_lots.TestUsChunkFeeApportionment",
     "features.forms.test_form8949_option_description.TestUsOptionRecordReachesForm8949",
     "features.engines.test_gains_json_decimals_option_units_and_split_timestamp.TestSplitAtTheTradesOwnTimestamp",
     "rules.ca.sl.test_bump_timing_and_day_thirty_balances.TestCanadaBalanceWalkClockOrder",
     "test_engine_gap_pins_ca", "test_engine_gap_pins_us",
     "test_engine_check_pins_ca", "test_engine_check_pins_us_corp",
     "test_engine_check_pins_us_trades", "test_engine_check_pins_us_misc",
     "test_engine_check_pins_transfers", "test_engine_check_pins_ca2",
     "test_engine_check_pins_us2"],
    [PY, "-m", "unittest", "-q",
     "test_engine_invariants", "rules.ca.sl.test_short_deferral_sign_and_sheltered_triggers.TestEngineShortAdjustSign",
     "features.engines.test_loss_row_with_no_time.TestEngineEmptyTimeNoCrash",
     "features.transfers.test_self_cancelling_transfer_drops.TestPairDropClustering",
     "rules.ca.sl.test_short_deferral_sign_and_sheltered_triggers.TestShelteredContributionIsATrigger",
     "features.summaries.test_estimate_refuses_negative_config_losses.TestEstimateConfigGuard",
     "features.parsers.test_ib_order_levels_and_questrade_venture_suffix.TestIbCancellations", "features.parsers.test_ib_order_levels_and_questrade_venture_suffix.TestVentureSuffix",
     "features.planning.test_harvest_schedule_and_watch_advisories.TestHarvestScheduleSemantics",
     "rules.ca.plan.test_full_exit_beside_an_old_sheltered_holding.TestExitableWithShelteredHolding",
     "features.planning.test_harvest_schedule_and_watch_advisories.TestWatchAdvisoryDiff",
     "rules.ca.corp.test_duplicated_split_in_unit_balance.TestBalanceOnSplitDedup",
     "features.forms.test_reconcile_slips_swaps_demo_detection_and_input_contracts.TestFullAuditRound2Fixes",
     "test_transfer_handling", "test_pipeline",
     "test_engines_expanded", "test_conservation",
     "test_blended_taxable", "test_usa_shorts",
     "test_canada_other_scope_isolation", "test_affiliated_flag",
     "test_missing_history_lib", "features.tools.test_stage_output_written_atomically.TestRunToFileAtomic",
     "features.listings.test_fees_view_counts_fee_rows.TestFeesIncludesFeeRows", "features.parsers.test_ib_dividend_reversal_and_refund_signs.TestIbReversalSigns",
     "features.planning.test_radar_across_clock_change.TestWashRadarDst",
     "rules.ca.plan.test_radar_violation_window.TestWashRadarViolationWindow", "rules.ca.corp.test_split_rows_shared_by_taxable_and_sheltered.TestSplitDedup",
     "features.missing_history.test_incomplete_history_openings"],
    [PY, "-m", "unittest", "-q", "test_engine_solver",
     "test_golden_ledgers.TestGoldenLedgers"],
]
# The variables git takes its repository's place from (`git rev-parse
# --local-env-vars`): a harness started from a git hook has some set
# (GIT_DIR, GIT_INDEX_FILE), and they would point a copy's git, and the
# harness's own, at the tree's store and index (TJ-4433). Every environment
# the harness gives git or the tests is without them (`_git_env`).
GIT_LOCATION_VARS = frozenset({
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG", "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_COUNT", "GIT_OBJECT_DIRECTORY", "GIT_DIR", "GIT_WORK_TREE",
    "GIT_IMPLICIT_WORK_TREE", "GIT_GRAFT_FILE", "GIT_INDEX_FILE", "GIT_NO_REPLACE_OBJECTS",
    "GIT_REPLACE_REF_BASE", "GIT_PREFIX", "GIT_SHALLOW_FILE", "GIT_COMMON_DIR"})


def _git_env() -> Dict[str, str]:
    """This process's environment without GIT_LOCATION_VARS."""
    return {k: v for k, v in os.environ.items() if k not in GIT_LOCATION_VARS}


# This tree's sources first: the venv's editable install points at the dev
# clone, so a copy of the tree (one per --shard part) would test the dev
# clone's code, not its own mutant (the baseline checks it).
TEST_ENV = dict(_git_env(), TAXJSON_FUZZ_BOOKS="30",
                PYTHONDONTWRITEBYTECODE="1",
                PYTHONPATH=os.pathsep.join(
                    p for p in (os.path.join(REPO, "src"),
                                os.environ.get("PYTHONPATH")) if p))
TEST_CWD = os.path.join(REPO, "tests")
# A set's limit: TIMEOUT, or five times the set's time on the unmutated
# tree when that is longer (the golden ledgers under N parallel parts), so
# a timeout (counted as a kill) is a mutant's, not the machine's load.
TIMEOUT = 120
SET_TIMEOUTS: List[float] = []

CMP_SWAPS = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE,
             ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
ARITH_SWAPS = {ast.Add: ast.Sub, ast.Sub: ast.Add,
               ast.Mult: ast.Div}
BOOL_SWAPS = {ast.And: ast.Or, ast.Or: ast.And}


def find_sites(src: str, lo: int, hi: int) -> Tuple[ast.Module, List[Tuple[str, Any]]]:
    tree = ast.parse(src)
    sites: List[Tuple[str, Any]] = []
    for node in ast.walk(tree):
        ln = getattr(node, "lineno", None)
        if ln is None or not (lo <= ln <= hi):
            continue
        if isinstance(node, ast.Compare) and len(node.ops) == 1 \
                and type(node.ops[0]) in CMP_SWAPS:
            sites.append(("cmp", node))
        elif isinstance(node, ast.BinOp) \
                and type(node.op) in ARITH_SWAPS:
            sites.append(("arith", node))
        elif isinstance(node, ast.BoolOp) \
                and type(node.op) in BOOL_SWAPS:
            sites.append(("bool", node))
        elif isinstance(node, ast.UnaryOp) \
                and isinstance(node.op, ast.USub) \
                and isinstance(node.operand, ast.Name):
            sites.append(("unaryneg", node))
        elif isinstance(node, ast.Constant) \
                and isinstance(node.value, (int, float)) \
                and node.value in (30, 31, 35, 0.001, 1e-6, 1e-9,
                                   0.005, 0.01):
            sites.append(("const", node))
    return tree, sites


def mutate(tree: ast.AST, kind: str, node: Any) -> Any:
    if kind == "cmp":
        node.ops[0] = CMP_SWAPS[type(node.ops[0])]()
    elif kind == "arith":
        node.op = ARITH_SWAPS[type(node.op)]()
    elif kind == "bool":
        node.op = BOOL_SWAPS[type(node.op)]()
    elif kind == "unaryneg":
        return node.operand           # drop the negation
    elif kind == "const":
        v = node.value
        node.value = (v + 1) if isinstance(v, int) else v * 10
    return node


# path -> original source of every file currently being mutated.
_ORIGINALS: Dict[str, str] = {}


def _restore_originals() -> None:
    """Write every registered original back (idempotent)."""
    for path, src in list(_ORIGINALS.items()):
        try:
            with open(path, "w") as f:
                f.write(src)
        except OSError as e:
            print(f"mutation_audit: COULD NOT RESTORE {path}: {e} — run "
                  f"`git checkout -- {path}`", file=sys.stderr)
            continue
        _ORIGINALS.pop(path, None)


def _on_signal(signum, _frame):
    _restore_originals()
    for pgid in list(_LIVE):
        with contextlib.suppress(OSError):
            os.killpg(pgid, signal.SIGKILL)
    _remove_copies()
    print(f"mutation_audit: stopped by signal {signum}; sources restored, the copies "
          f"removed", file=sys.stderr)
    os._exit(128 + signum)


# The process groups of the sets running now (one per copy), killed on a signal.
_LIVE: Set[int] = set()


def install_restore_handlers() -> None:
    """SIGTERM / SIGHUP used to kill the run between writing a mutant
    and the try/finally that restores it, leaving a de-commented,
    mutated engine behind (audit S025-00). SIGINT too: a run's copies'
    test processes are in sessions of their own, out of a Ctrl-C's reach,
    and are killed here (`_LIVE`)."""
    import atexit
    atexit.register(_restore_originals)
    atexit.register(_remove_copies)
    for sig in (signal.SIGTERM, getattr(signal, "SIGHUP", None), signal.SIGINT):
        if sig is not None:
            signal.signal(sig, _on_signal)


def _env_for(root: str) -> Dict[str, str]:
    """TEST_ENV with `root`'s src/ first on PYTHONPATH: a copy tests its own code."""
    return dict(TEST_ENV, PYTHONPATH=os.pathsep.join(
        p for p in (os.path.join(root, "src"), os.environ.get("PYTHONPATH")) if p))


def _run_set(cmd: Sequence[str], timeout: float,
             env: Optional[Dict[str, str]] = None, root: Optional[str] = None) -> int:
    """One test set's exit status. It runs in a process group of its own,
    so a timeout stops what it started too (the golden ledgers' `tjs
    run`), not only the unittest process; the timeout is then re-raised.
    `env`: TEST_ENV unless given (the line trace's); `root`: the tree whose
    tests/ and src/ it runs (a mutant's copy), this one unless given."""
    cwd, env = ((TEST_CWD, env or TEST_ENV) if root is None
                else (os.path.join(root, "tests"), env or _env_for(root)))
    proc = subprocess.Popen(cmd, cwd=cwd, env=env,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True)
    _LIVE.add(proc.pid)
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        raise
    finally:
        _LIVE.discard(proc.pid)
    if rc < 0:                          # killed: what it started goes too
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)
    return rc


def run_tests(timeout=None):
    """'killed' at the first test set that fails, 'survived' when every
    set passes (a set's timeout raises subprocess.TimeoutExpired). Each
    set has its own limit (SET_TIMEOUTS) unless `timeout` is given."""
    for i, cmd in enumerate(TEST_CMDS):
        limit = timeout or (SET_TIMEOUTS[i] if i < len(SET_TIMEOUTS)
                            else TIMEOUT)
        if _run_set(cmd, limit) != 0:
            return "killed"
    return "survived"


def _check_imports_here(root: Optional[str] = None) -> None:
    """Exit unless the sets import taxjson from this tree (or the copy `root`)."""
    src = os.path.join(root or REPO, "src") + os.sep
    where = subprocess.run(
        [PY, "-P", "-c", "import taxjson; print(taxjson.__file__)"],
        cwd=TEST_CWD if root is None else os.path.join(root, "tests"),
        env=TEST_ENV if root is None else _env_for(root), capture_output=True, text=True)
    if not os.path.abspath(where.stdout.strip() or "?").startswith(src):
        print(f"taxjson imports from {where.stdout.strip() or '?'}, not "
              f"{src} — aborting")
        sys.exit(1)


def baseline():
    """Exit unless the unmutated tree passes every set, importing taxjson
    from this tree; record each set's limit (SET_TIMEOUTS)."""
    _check_imports_here()
    SET_TIMEOUTS.clear()
    for cmd in TEST_CMDS:
        t0 = time.time()
        if _run_set(cmd, 600) != 0:
            print("BASELINE FAILS — aborting")
            sys.exit(1)
        SET_TIMEOUTS.append(max(TIMEOUT, 5 * (time.time() - t0)))


# --------------------------------------------------- the parsers (step 16)

class Site(NamedTuple):
    """One mutable site of a parser target: its file (repo-relative), its
    place in `find_sites`' list for that file (`pos`), its kind, its lines and the
    function it sits in (`<module>` outside one)."""
    rel: str
    pos: int
    kind: str
    line: int
    end_line: int
    func: str


def _function_spans(tree: ast.AST) -> List[Tuple[int, int, str]]:
    """[(first line, last line, qualname)] of every function in `tree`,
    classes and enclosing functions in the name."""
    out: List[Tuple[int, int, str]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qual = prefix + child.name
                out.append((child.lineno, child.end_lineno or child.lineno, qual))
                walk(child, qual + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, prefix + child.name + ".")
            else:
                walk(child, prefix)
    walk(tree, "")
    return out


def parser_sites(rels: Sequence[str]) -> List[Site]:
    """Every mutable site (not `unaryneg`, which the harness skips) of the
    files `rels`, in file order then `find_sites` order."""
    out: List[Site] = []
    for rel in rels:
        with open(os.path.join(REPO, rel), encoding="utf-8") as f:
            src = f.read()
        tree, sites = find_sites(src, 1, len(src.splitlines()))
        spans = _function_spans(tree)
        for i, (kind, node) in enumerate(sites):
            if kind == "unaryneg":
                continue
            inner = [s for s in spans if s[0] <= node.lineno <= s[1]]
            func = max(inner)[2] if inner else "<module>"
            out.append(Site(rel, i, kind, node.lineno,
                            getattr(node, "end_lineno", None) or node.lineno, func))
    return out


def stratified_sample(sites: Sequence[Site], n: int, seed: int) -> List[int]:
    """The indices (sorted) into `sites` of a sample of `n`, the same for one
    seed: every site when there are `n` or fewer; else each function (a
    stratum: its file and qualname) gets one site while there are no more
    functions than `n`, the rest of `n` shared in proportion to the
    functions' other sites (largest remainder, ties in file order), each
    function's share drawn at random; with more functions than `n`, `n` of
    them drawn weighted by their sites (a 45-site function weighs 45 times a
    1-site one: Efraimidis and Spirakis' keys), one site each."""
    if len(sites) <= n:
        return list(range(len(sites)))
    rng = random.Random(seed)
    groups: Dict[Tuple[str, str], List[int]] = {}
    for i, s in enumerate(sites):
        groups.setdefault((s.rel, s.func), []).append(i)
    keys = list(groups)
    if len(keys) >= n:
        weight = {k: rng.random() ** (1.0 / len(groups[k])) for k in keys}
        drawn = sorted(keys, key=lambda k: -weight[k])[:n]
        quota = dict.fromkeys(sorted(drawn, key=keys.index), 1)
    else:
        quota = dict.fromkeys(keys, 1)
        rest, spare = n - len(keys), {k: len(groups[k]) - 1 for k in keys}
        total = sum(spare.values())
        for k in keys:
            quota[k] += rest * spare[k] // total
        left = n - sum(quota.values())
        order = sorted(keys, key=lambda k: (-(rest * spare[k] % total), keys.index(k)))
        for k in order[:left]:
            quota[k] += 1
    picked: List[int] = []
    for k in keys:
        if k in quota:
            picked += rng.sample(groups[k], quota[k])
    return sorted(picked)


def candidate_modules() -> List[str]:
    """Every test module (dotted, below tests/), sorted: the trace runs them
    all, so a module that never names a target is found when it runs one. A
    folder below tests/ without `__init__.py` (data, helpers) holds none."""
    out = []
    root = Path(TEST_CWD)
    for path in sorted(root.rglob("test_*.py")):
        rel = path.relative_to(root)
        folders = [root.joinpath(*rel.parts[:k]) for k in range(1, len(rel.parts))]
        if any(p.startswith("_") for p in rel.parts[:-1]) or not all(
                (d / "__init__.py").is_file() for d in folders):
            continue
        out.append(".".join(rel.with_suffix("").parts))
    return sorted(out)


# The line trace's instrument, appended to each traced file while the trace
# runs (`instrument_source`; the file is restored after, as a mutant is): any
# process that imports the file records the lines of its own that it runs,
# whatever PYTHONPATH a test's helper gave it, so a stage a test starts is
# traced too. Where the records go is MUTATION_TRACE_OUT, read from the
# process's environment, else from its own first environment (/proc: a test
# that cleared os.environ before the import), else from its nearest
# ancestor's (a child a helper gave an environment of its own). A process of
# the trace that finds none (an orphan whose parent has exited, /proc
# unreadable) writes to the trace's LOST folder, which the trace counts:
# nothing it ran is silently lost. A process records itself started, writes
# its lines as it goes (every FLUSH new lines, or a new one FLUSH_SECONDS after
# its last write), at its end and on a SIGTERM no one else handles, and
# records itself done: one killed or ended by os._exit is counted as
# unflushed, and a run with one in a module of A or B is not recorded. Only
# the file's first LIMIT lines count (its own code, not the instrument). It
# starts after the file's own lines ran, so it records the lines of the
# functions a process calls: a node that runs at import is cut by
# `cut_to_line`'s `body`. sys.monitoring (3.12), else sys.settrace.
_INSTRUMENT = r"""


def _mutation_trace_hook(limit, lost):
    # Appended by scripts/mutation_audit.py for its line trace; removed after.
    import os
    import sys
    out = os.environ.get("MUTATION_TRACE_OUT")
    pid = os.getpid()
    while not out and pid > 1:
        try:
            with open("/proc/%d/environ" % pid, "rb") as f:
                env = dict(kv.split(b"=", 1) for kv in f.read().split(b"\0") if b"=" in kv)
            out = env.get(b"MUTATION_TRACE_OUT", b"").decode() or None
            with open("/proc/%d/stat" % pid, encoding="utf-8") as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    if not out:
        if not os.path.isdir(lost):
            return              # no trace runs: the file is used, not traced
        out = lost
    state = sys.__dict__.setdefault("_mutation_trace_state", {"files": {}, "hits": {}})
    files, hits = state["files"], state["hits"]
    files[__file__] = limit
    if state.get("on"):
        return
    state["on"] = True
    import atexit
    import json
    # The mode is a name, not a literal: while the instrument is in a file,
    # the tree's check of src/'s file writes reads it too
    # (tests/features/configuration/test_generated_state_never_follows_links.py).
    mode = "w"

    def write(suffix, data):
        name = os.path.join(out, "%d%s" % (os.getpid(), suffix))
        with open(name + ".part", mode, encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(name + ".part", name)

    def flush():
        write(".json", {k: sorted(v) for k, v in list(hits.items())})
    write(".started", {})
    import time
    new = [0, time.monotonic()]

    def hit(path, ln):
        if ln not in hits.setdefault(path, set()):
            hits[path].add(ln)
            new[0] += 1
            if new[0] >= __FLUSH__ or time.monotonic() - new[1] > __EVERY__:
                new[:] = [0, time.monotonic()]
                flush()
    mon = getattr(sys, "monitoring", None)
    tool = None
    for t in ((3, 4, 5) if mon is not None else ()):
        try:
            mon.use_tool_id(t, "mutation_audit")
            tool = t
            break
        except ValueError:
            pass

    def mine(code):
        return code.co_firstlineno <= files.get(code.co_filename, 0)
    if tool is not None:
        def start(code, offset):
            if mine(code):
                mon.set_local_events(tool, code, mon.events.LINE)
            return mon.DISABLE

        def line(code, ln):
            if ln <= files[code.co_filename]:
                hit(code.co_filename, ln)
            return mon.DISABLE
        mon.register_callback(tool, mon.events.PY_START, start)
        mon.register_callback(tool, mon.events.LINE, line)
        mon.set_events(tool, mon.events.PY_START)

        def stop():
            mon.set_events(tool, 0)
    else:
        import threading

        def local(frame, event, arg):
            if event == "line" and frame.f_lineno <= files[frame.f_code.co_filename]:
                hit(frame.f_code.co_filename, frame.f_lineno)
            return local

        def glob(frame, event, arg):
            return local if mine(frame.f_code) else None
        sys.settrace(glob)
        threading.settrace(glob)

        def stop():
            sys.settrace(None)

    def dump():
        stop()
        flush()
        write(".done", {})
    atexit.register(dump)
    import signal

    def on_term(signum, frame):
        dump()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)
    try:
        if signal.getsignal(signal.SIGTERM) == signal.SIG_DFL:
            signal.signal(signal.SIGTERM, on_term)
    except (ValueError, OSError):
        pass                    # imported off the main thread: atexit alone


_mutation_trace_hook(__LIMIT__, __LOST__)
del _mutation_trace_hook
"""
# A process writes its lines every FLUSH new ones, or a new one FLUSH_SECONDS
# after its last write (and at its end, and on SIGTERM).
FLUSH = 64
FLUSH_SECONDS = 0.5
# The trace's own folders: each module's records, and the lost ones'.
_HITS, _LOST = "hits", "lost"


def instrument_source(src: str, lost: str = "") -> str:
    """`src` with the trace's instrument appended (its own lines unmoved);
    `lost`, the trace's folder for a process that cannot tell its module."""
    return (src.rstrip("\n") + "\n"
            + _INSTRUMENT.replace("__LIMIT__", str(len(src.splitlines())))
            .replace("__LOST__", repr(lost)).replace("__FLUSH__", str(FLUSH))
            .replace("__EVERY__", repr(FLUSH_SECONDS)))


def _records(out: str) -> Tuple[Dict[str, Set[int]], int]:
    """({file: the lines run} of every process's record in `out`, the
    processes that started and never recorded their end)."""
    lines: Dict[str, Set[int]] = {}
    names = os.listdir(out) if os.path.isdir(out) else []
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(out, name), encoding="utf-8") as f:
                got = json.load(f)
        except (OSError, ValueError):
            continue
        for path, hit in got.items():
            lines.setdefault(os.path.relpath(os.path.abspath(path), REPO), set()).update(hit)
    done = {n[:-len(".done")] for n in names if n.endswith(".done")}
    unflushed = sum(1 for n in names if n.endswith(".started") and n[:-len(".started")] not in done)
    return lines, unflushed


def _trace_one(mod: str, rels: Sequence[str], out: str) -> Dict[str, Any]:
    """One test module's record under the instrument: its exit status, its
    seconds, the lines of `rels` its processes ran and how many of them
    ended before their last record (`unflushed`)."""
    os.makedirs(out)
    env = dict(TEST_ENV, MUTATION_TRACE_OUT=out)
    t0 = time.time()
    rc: Any
    try:
        rc = _run_set(_unittest(mod), TRACE_TIMEOUT, env=env)
    except subprocess.TimeoutExpired:
        rc = "timeout"
    lines, unflushed = _records(out)
    return {"rc": rc, "seconds": round(time.time() - t0, 2),
            "lines": {r: sorted(lines[r]) for r in rels if r in lines},
            "unflushed": unflushed}


def trace_target(rels: Sequence[str], modules: Sequence[str], jobs: int = 1
                 ) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """({module: `_trace_one`'s record} for each of `modules`, `jobs` at
    once, the processes that could not tell their module), the files `rels`
    instrumented in place (restored after, or on a signal)."""
    from concurrent.futures import ThreadPoolExecutor
    install_restore_handlers()
    tmp = tempfile.mkdtemp(prefix="mutation_trace_")
    lost = os.path.join(tmp, _LOST)
    os.makedirs(lost)
    paths = [os.path.join(REPO, rel) for rel in rels]
    try:
        for path in paths:
            with open(path, encoding="utf-8") as f:
                src = f.read()
            _ORIGINALS[path] = src
            with open(path, "w", encoding="utf-8") as f:
                f.write(instrument_source(src, lost))
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            recs = list(ex.map(lambda m: _trace_one(m, rels, os.path.join(tmp, _HITS, m)),
                               modules))
        lost_n = sum(1 for n in os.listdir(lost) if n.endswith(".started"))
    finally:
        _restore_originals()
        shutil.rmtree(tmp, ignore_errors=True)
    return dict(zip(modules, recs)), lost_n


# The folders the tools write for themselves (bytecode, the linters' caches):
# no input of a test, ignored by git, so neither the fingerprint nor a
# record's check of the tree reads them.
_CACHE_DIRS = frozenset({"__pycache__", ".ruff_cache", ".mypy_cache", ".pytest_cache"})


def _cache_file(rel: str) -> bool:
    """A file a tool writes for itself (`_CACHE_DIRS`, bytecode)."""
    return bool(_CACHE_DIRS & set(rel.split("/"))) or rel.endswith((".pyc", ".pyo"))


def _git_files(*args: str, tops: Sequence[str] = ("src", "tests")) -> Optional[List[str]]:
    """`git ls-files -z` with `args` below `tops`, as repo-relative names
    (the tools' caches left out: `_cache_file`), or None without git."""
    r = subprocess.run(["git", "-C", REPO, "ls-files", "-z", *args, "--", *tops],
                       capture_output=True, env=_git_env())
    if r.returncode != 0:
        return None
    names = r.stdout.decode("utf-8", "surrogateescape").split("\0")
    return [n for n in names if n and not _cache_file(n)]


def _tree_files() -> List[str]:
    """The files below src/ and tests/ (repo-relative, sorted): every one git
    knows of, tracked, untracked or ignored (a `tmp*/` folder's json.py, an
    ignored taxjson.toml: what a test reads is no less read for being
    ignored), bytecode left out; every file there in a tree without git."""
    known = _git_files("--cached", "--others")
    ignored = _git_files("--others", "--ignored", "--exclude-standard")
    if known is not None and ignored is not None and known:
        return sorted({n for n in known + ignored if os.path.isfile(os.path.join(REPO, n))})
    return sorted(p.relative_to(REPO).as_posix() for top in ("src", "tests")
                  for p in Path(REPO, top).rglob("*")
                  if p.is_file() and not _cache_file(p.relative_to(REPO).as_posix()))


def _fingerprint() -> str:
    """What a saved trace was made of: every file below src/ and tests/ (the
    targets, what calls them, the tests and their helpers and data,
    `_tree_files`) and the instrument; any change traces again."""
    h = hashlib.sha256(_INSTRUMENT.encode())
    for rel in _tree_files():
        with open(os.path.join(REPO, rel), "rb") as f:
            h.update(rel.encode() + b"\0" + f.read())
    return h.hexdigest()


# What the last `load_trace` read or made beside its modules: the processes
# that could not tell their module, and those that ended before their last
# record (a run's report says both).
TRACE_LOSSES: Dict[str, int] = {"lost": 0, "unflushed": 0}
# The test modules the last `load_trace`'s trace ran at once (TJ-4373): its
# seconds (which modules are quick, the order a set runs in, B's limits) are
# the machine's under that load, so a run is the same for any --jobs only for
# one saved trace; the report and the record say which: its jobs, and the
# sha256 of its saved file (TJ-4430).
TRACE_MADE: Dict[str, Any] = {"jobs": 0, "sha256": ""}


def load_trace(jobs: int = 1, retrace: bool = False,
               path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """The trace of every test module (`candidate_modules`) over every
    target's files (TRACED_FILES): the one saved at `path` (default:
    mutation_trace_all.json beside the report) when it was made of the same
    tree and holds every candidate, else a new one, saved there. Any
    target's run reuses it, unless it does not say the jobs it was made
    with. Sets TRACE_LOSSES and TRACE_MADE (the jobs, the file's sha256)."""
    modules = candidate_modules()
    path = path or os.path.join(HERE, "..", "mutation_trace_all.json")
    fp = _fingerprint()
    if not retrace and os.path.isfile(path):
        with open(path, "rb") as fb:
            raw = fb.read()
        saved = json.loads(raw)
        held = saved.get("modules") or {}
        made = saved.get("jobs")
        made = made if isinstance(made, int) and not isinstance(made, bool) and made > 0 else 0
        if saved.get("fingerprint") == fp and set(modules) <= set(held) and made:
            TRACE_LOSSES.update(lost=int(saved.get("lost") or 0),
                                unflushed=sum(int(r.get("unflushed") or 0)
                                              for r in held.values()))
            TRACE_MADE.update(jobs=made, sha256=hashlib.sha256(raw).hexdigest())
            return dict(held)
        if saved.get("fingerprint") == fp and not set(modules) <= set(held):
            print(f"the saved trace holds {len(set(modules) & set(held))} of the "
                  f"{len(modules)} test modules: tracing again", flush=True)
        elif saved.get("fingerprint") == fp:
            print("the saved trace does not say the --jobs it was made with: tracing again",
                  flush=True)
    print(f"tracing {len(modules)} test modules ({jobs} at a time)", flush=True)
    recs, lost = trace_target(TRACED_FILES, modules, jobs)
    text = json.dumps({"files": list(TRACED_FILES), "fingerprint": fp, "jobs": jobs,
                       "lost": lost, "modules": recs}, indent=1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    TRACE_LOSSES.update(lost=lost, unflushed=sum(r["unflushed"] for r in recs.values()))
    TRACE_MADE.update(jobs=jobs, sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())
    return recs


def _body_lines(rels: Sequence[str]) -> Dict[str, Set[int]]:
    """{file: the lines inside its functions' bodies}: a module that ran
    only the file's import (its defs, its constants) did not run the parser."""
    out: Dict[str, Set[int]] = {}
    for rel in rels:
        with open(os.path.join(REPO, rel), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        out[rel] = {ln for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    for ln in range(node.body[0].lineno, (node.end_lineno or node.lineno) + 1)}
    return out


def parser_sets(trace: Dict[str, Dict[str, Any]], body: Dict[str, Set[int]]
                ) -> Tuple[List[str], List[str], List[str], List[str]]:
    """(A, B, C, failed) from a trace (`load_trace`) and `_body_lines`: the
    modules that passed and ran a line inside a function of the target, the
    golden ones (GOLDEN_MODULES) C, the others A when they took A_SECONDS or
    less, else B; `failed`, those that failed or timed out unmutated (no set
    holds them: they would kill every mutant). A module that ran no test
    (unittest's status 5: an empty module) is in none: it kills nothing."""
    a: List[str] = []
    b: List[str] = []
    c: List[str] = []
    failed: List[str] = []
    for mod in sorted(trace):
        rec = trace[mod]
        if rec["rc"] == NO_TESTS_RAN:
            continue
        if rec["rc"] != 0:
            failed.append(mod)
            continue
        if not any(set(lines) & body.get(rel, set()) for rel, lines in rec["lines"].items()):
            continue
        if mod in GOLDEN_MODULES:
            c.append(mod)
        elif rec["seconds"] <= A_SECONDS:
            a.append(mod)
        else:
            b.append(mod)
    return a, b, c, failed


def cut_to_line(mods: Sequence[str], trace: Dict[str, Dict[str, Any]], rel: str,
                lo: int, hi: int, body: Optional[Set[int]] = None) -> List[str]:
    """The modules of `mods`, in order, whose trace ran a line from `lo` to
    `hi` of `rel` (a mutated node's lines): set B for that mutant. With
    `body` (the file's `_body_lines`), a node outside every function body (a
    constant, a class attribute, a default: it runs at import, before the
    instrument at the file's end starts) is run by every module that ran a
    line of the file."""
    span = set(range(lo, hi + 1))
    if body is not None and not span & body:
        return [m for m in mods if trace[m]["lines"].get(rel)]
    return [m for m in mods if span & set(trace[m]["lines"].get(rel, ()))]


NO_TESTS_RAN = 5               # unittest's exit status when no test ran


def _unittest(*mods: str) -> List[str]:
    return [PY, "-m", "unittest", "-q", "_hermetic", *mods]


def record_commit(commit: Optional[str] = None
                  ) -> Tuple[Optional[str], Optional[str], str]:
    """(the commit a run's record names, its tree's id, '') when the tree is
    that commit's: `commit` (--commit) or HEAD, resolved in this repository,
    with no file below src/, tests/ or scripts/ changed, added or removed
    against it, nor one there that git ignores (a `tmp*/` folder's json.py, an
    ignored taxjson.toml: a test reads it all the same), the tools' caches
    apart; else (None, None, why): such a run is not recorded. The tree's id
    outlives the commit's: a rebase-merge gives a landed commit a new id."""
    want = commit or "HEAD"
    r = subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "--quiet",
                        want + "^{commit}"], capture_output=True, text=True, env=_git_env())
    sha = r.stdout.strip()
    if r.returncode != 0 or not sha:
        return None, None, f"{want} is no commit of this repository (a copy without its history?)"
    tree = subprocess.run(["git", "-C", REPO, "rev-parse", "--verify", "--quiet",
                           sha + "^{tree}"], capture_output=True, text=True, env=_git_env())
    tops = ("src", "tests", "scripts")
    changed = subprocess.run(["git", "-C", REPO, "diff", "--name-only", sha, "--", *tops],
                             capture_output=True, text=True, env=_git_env())
    added = _git_files("--others", "--exclude-standard", tops=tops)
    ignored = _git_files("--others", "--ignored", "--exclude-standard", tops=tops)
    if (tree.returncode != 0 or changed.returncode != 0 or added is None
            or ignored is None):
        return None, None, f"git cannot compare the tree with {sha[:10]}"
    files = changed.stdout.split() + added
    if files:
        return None, None, (f"the tree is not {sha[:10]}'s: {len(files)} file(s) below src/, "
                            f"tests/ or scripts/ differ, the first {files[0]}")
    if ignored:
        return None, None, (f"the tree is not {sha[:10]}'s: {len(ignored)} file(s) git ignores "
                            f"below src/, tests/ or scripts/ (the tests read them all the "
                            f"same), the first {ignored[0]}")
    return sha[:10], tree.stdout.strip(), ""


# A set's runner (`_run_modules`): what `python -m unittest -f _hermetic MOD...`
# does, the modules loaded first and run in the order given, stopping at the
# first failure, and saying in OUT which module runs and which failed (unittest
# names a test, and a module that cannot load by its last word alone). One
# that cannot be loaded fails in its turn.
_RUNNER = r"""
import json, os, sys, unittest
out, mods = sys.argv[1], sys.argv[2:]
sys.argv[:] = ["python -m unittest", "-q", "_hermetic", *mods]    # as the trace ran them


def said(**kw):
    with open(out + ".part", "w", encoding="utf-8") as f:
        json.dump(kw, f)
    os.replace(out + ".part", out)
import _hermetic
loader = unittest.TestLoader()
suites = []
for m in mods:
    said(running=m)
    try:
        suites.append((m, loader.loadTestsFromName(m)))
    except BaseException:
        suites.append((m, None))
runner = unittest.TextTestRunner(stream=sys.stderr, verbosity=0, failfast=True)
for m, suite in suites:
    said(running=m)
    if suite is None or not runner.run(suite).wasSuccessful():
        said(failed=m)
        sys.exit(1)
said(done=True)
"""


class CopyCrashed(Exception):
    """A set's process ended by a signal no one in the run sent (the OOM
    killer, a user's kill): its result is no mutant's."""


# The signals that end a set's process from outside (a timeout's SIGKILL is
# the run's own, and raises TimeoutExpired instead).
_OUTSIDE = frozenset(-int(s) for s in (signal.SIGKILL, signal.SIGTERM, signal.SIGINT,
                                       getattr(signal, "SIGHUP", signal.SIGTERM)))


def _said(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            got = json.load(f)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def _run_modules(mods: Sequence[str], limit: float, root: Optional[str] = None
                 ) -> Tuple[str, str]:
    """('pass', ''), ('fail', the first module that failed) or ('timeout',
    the module that ran when `limit` ran out): `mods` in one process
    (`_RUNNER`), in their order, stopping at the first failure. CopyCrashed
    when a signal from outside ended it."""
    fd, out = tempfile.mkstemp(prefix="set_", suffix=".json", dir=_scratch())
    os.close(fd)
    try:
        try:
            rc = _run_set([PY, "-c", _RUNNER, out, *mods], limit, root=root)
        except subprocess.TimeoutExpired:
            return "timeout", str(_said(out).get("running") or "")
        if rc in _OUTSIDE:
            raise CopyCrashed(f"a set's process was ended by signal {-rc}")
        got = _said(out)
        if rc == 0 and got.get("done"):
            return "pass", ""
        # Ended with no word (os._exit in a test): the module that was running.
        return "fail", str(got.get("failed") or got.get("running") or (mods[0] if mods else ""))
    finally:
        for p in (out, out + ".part"):
            with contextlib.suppress(OSError):
                os.remove(p)


def _passes(mods: Sequence[str], limit: float, root: Optional[str] = None) -> bool:
    """Whether `mods` pass (in `root`'s tree); a process killed from outside
    is run once more, twice is the end of the run."""
    for _attempt in (1, 2):
        with contextlib.suppress(CopyCrashed):
            return _run_modules(mods, limit, root)[0] == "pass"
    print("the control's copy was killed from outside twice — aborting")
    sys.exit(1)


# What a copy of the tree leaves out: git's folder (the copy gets a store of
# its own, `_copy_git`), the linked venv, the tools' caches, bytecode and this
# harness's outputs beside the report.
_COPY_SKIP = frozenset({".git", "venv"}) | _CACHE_DIRS
# The folders the run's copies are in, removed at its end or on a signal.
_COPY_BASES: List[str] = []
# A copy whose process is killed from outside is made again and its mutant
# rerun this many times; then the mutant is "crashed" (no record).
RETRIES = 2


def _git_out(*args: str, cwd: Optional[str] = None) -> Optional[str]:
    """`git -C cwd args`'s output, stripped (this tree's unless `cwd`), or
    None when git fails."""
    r = subprocess.run(["git", "-C", cwd or REPO, *args], capture_output=True, text=True,
                       env=_git_env())
    return r.stdout.strip() if r.returncode == 0 else None


def _copy_git(dest: str) -> bool:
    """Give the copy `dest` a git store of its own that sees it as this
    tree's git sees this tree (TJ-4351): its HEAD, its refs (tags too) and its
    index, the objects read through `objects/info/alternates` from this
    tree's store, which nothing in the copy writes to. A module that runs git
    on the tree (`test_parser_check`'s checkout, the release tests' tags)
    then runs in a copy as in the tree, and a mutant shows as a changed file,
    as it would there. Nothing when this tree is no git checkout of its own
    (a `git archive` copy): a module that needs git fails in both. False,
    and the copy left without git, when git could not make it."""
    top = _git_out("rev-parse", "--show-toplevel")
    if not top or os.path.realpath(top) != os.path.realpath(REPO):
        return True                     # no checkout of its own: nor is the copy
    common = _git_out("rev-parse", "--path-format=absolute", "--git-common-dir")
    index = _git_out("rev-parse", "--path-format=absolute", "--git-path", "index")
    head = _git_out("symbolic-ref", "-q", "HEAD") or _git_out("rev-parse", "--verify", "HEAD")
    refs = _git_out("for-each-ref", "--format=%(objectname) %(refname)")
    fmt = _git_out("rev-parse", "--show-object-format") or "sha1"
    git_dir = os.path.join(dest, ".git")
    try:
        if not (common and index and head and refs is not None):
            raise OSError("git could not read the tree's HEAD, refs or index")
        if subprocess.run(["git", "init", "-q", "--template=", f"--object-format={fmt}",
                           dest], capture_output=True, env=_git_env()).returncode != 0:
            raise OSError("git init failed")
        with open(os.path.join(git_dir, "objects", "info", "alternates"), "w",
                  encoding="utf-8") as f:
            f.write(os.path.join(common, "objects") + "\n")
        with open(os.path.join(git_dir, "packed-refs"), "w", encoding="utf-8") as f:
            f.write("".join(line + "\n" for line in refs.splitlines() if line))
        with open(os.path.join(git_dir, "HEAD"), "w", encoding="utf-8") as f:
            f.write((f"ref: {head}" if head.startswith("refs/") else head) + "\n")
        for have, put in ((index, "index"), (os.path.join(common, "shallow"), "shallow")):
            if os.path.isfile(have):    # a shallow clone's copy is shallow too
                shutil.copyfile(have, os.path.join(git_dir, put))
        # The index's file times are the tree's: refreshed once here, so a
        # test's git does not hash every file again.
        subprocess.run(["git", "-C", dest, "update-index", "-q", "--refresh"],
                       capture_output=True, env=_git_env())
        if _git_out("rev-parse", "HEAD", cwd=dest) != _git_out("rev-parse", "HEAD"):
            raise OSError("the copy's HEAD is not the tree's")
    except OSError as e:
        shutil.rmtree(git_dir, ignore_errors=True)
        print(f"note: a copy has no git ({e}): a module that needs git fails there and "
              f"is set aside")
        return False
    return True


def _make_copy(dest: str) -> str:
    """`dest`, a fresh copy of the tree (`_COPY_SKIP` left out) with a git
    store of its own (`_copy_git`), its src/ and tests/ compiled once. The sets run under PYTHONDONTWRITEBYTECODE
    (TEST_ENV: no mutant's bytecode is ever written), so a copy without its
    bytecode compiled every module in every process, each `tjs` a test
    starts too: whole sets ran 1.6 to 1.9 times slower (the 5d proof).
    Checked against each file's hash, never its time, so a mutated or
    restored file is compiled from its source. One process (no -j: its
    workers' socket under a long TMPDIR is a path too long for AF_UNIX)."""
    top = os.path.abspath(REPO)
    outputs = {os.path.basename(REPORT), "mutation_trace_all.json"}

    def ignore(folder: str, names: List[str]) -> Set[str]:
        skip = {n for n in names if n in _COPY_SKIP or n.endswith((".pyc", ".pyo"))}
        return skip | (outputs & set(names) if os.path.abspath(folder) == top else set())
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(REPO, dest, symlinks=True, ignore=ignore)
    if os.path.exists(os.path.join(REPO, "venv")):
        os.symlink(os.path.realpath(os.path.join(REPO, "venv")), os.path.join(dest, "venv"))
    _copy_git(dest)
    rc = subprocess.run([PY if os.path.exists(PY) else sys.executable, "-I", "-m", "compileall",
                         "-q", "--invalidation-mode", "checked-hash",
                         *(p for p in (os.path.join(dest, d) for d in ("src", "tests"))
                           if os.path.isdir(p))],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        check=False).returncode
    if rc:
        print(f"note: a copy's bytecode was not all compiled (compileall exit {rc}): "
              f"its sets run slower")
    return dest


def make_copies(n: int) -> List[str]:
    """`n` copies of the tree, one per mutant run at once, each in a folder
    named as the tree's (a test may read its folder's name), in a temporary
    folder `_remove_copies` removes."""
    base = _scratch()
    return [_make_copy(os.path.join(base, str(k), os.path.basename(os.path.abspath(REPO))))
            for k in range(n)]


def _remove_copies() -> None:
    while _COPY_BASES:
        shutil.rmtree(_COPY_BASES.pop(), ignore_errors=True)


def _scratch() -> str:
    """The run's own temporary folder (a set's runner writes there), removed
    with the copies."""
    if not _COPY_BASES:
        _COPY_BASES.append(tempfile.mkdtemp(prefix="mutation_copies_"))
    return _COPY_BASES[0]


def _over_copies(seqs: Sequence[int], copies: List[str], one: Callable[[int, str], Any],
                 done: Callable[[int, Any, float], None]) -> None:
    """`one(seq, copy)` for each of `seqs`, one at a time in each copy (so
    len(copies) at once), and `done(seq, result, seconds)` as each ends,
    under one lock. A copy whose run raised CopyCrashed or OSError (a set's
    process killed from outside, a file it could not write) is made again
    and the mutant rerun, RETRIES times at most; then its result is
    ('crashed', why, []). Nothing else differs with the number of copies."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    lock = threading.Lock()
    todo = list(seqs)

    def work(k: int) -> None:
        while True:
            with lock:
                if not todo:
                    return
                seq = todo.pop(0)
            t0 = time.time()
            got: Any = None
            for attempt in range(RETRIES + 1):
                try:
                    got = one(seq, copies[k])
                    break
                except (CopyCrashed, OSError) as e:
                    print(f"copy {k + 1}: {e} (#{seq}): the copy is made again"
                          + (", and the mutant run again" if attempt < RETRIES else ""),
                          flush=True)
                    copies[k] = _make_copy(copies[k])
                    got = ("crashed", f"{e}, {attempt + 1} times", [])
            with lock:
                done(seq, got, time.time() - t0)
    with ThreadPoolExecutor(max_workers=max(1, len(copies))) as ex:
        list(ex.map(work, range(len(copies))))


def control_run(rels: Sequence[str], a: Sequence[str], b: Sequence[str],
                c: Sequence[str], jobs: int = 1, root: Optional[str] = None
                ) -> Tuple[List[str], Dict[str, float]]:
    """The control, once per run, in the tree `root` (a copy; this one unless
    given): the files `rels` rewritten by `ast.unparse` with no mutation (the
    rewrite each mutant gets: no comments, other line numbers), and under it
    set A whole, each module of `b` (the modules of the sample's cuts) alone,
    `jobs` at once, and set C whole; a whole set that fails is run module by
    module. (the modules that fail, set aside like those that failed in the
    trace, in every copy; the seconds of 'A', of 'C' and of each module of
    `b`). A set that still fails without them, or fails whole though no
    module fails alone, aborts the run: every mutant would be killed."""
    out: List[str] = []
    secs: Dict[str, float] = {}
    root = root or REPO
    try:
        for rel in rels:
            path = os.path.join(root, rel)
            with open(path, encoding="utf-8") as f:
                src = f.read()
            _ORIGINALS[path] = src
            with open(path, "w", encoding="utf-8") as f:
                f.write(ast.unparse(ast.parse(src)))
        for name, mods in (("A", list(a)), ("C", list(c))):
            t0 = time.time()
            if mods and not _passes(mods, 1800, root):
                bad = [m for m in mods if not _passes([m], 1800, root)]
                rest = [m for m in mods if m not in bad]
                t0 = time.time()
                if not bad or (rest and not _passes(rest, 1800, root)):
                    print(f"CONTROL FAILS (set {name}, the targets unparsed and unmutated) — "
                          f"aborting")
                    sys.exit(1)
                out += bad
            secs[name] = time.time() - t0
        from concurrent.futures import ThreadPoolExecutor

        def alone(m: str) -> Tuple[bool, float]:
            t0 = time.time()
            return _passes([m], TRACE_TIMEOUT, root), time.time() - t0
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            for m, (ok, took) in zip(b, ex.map(alone, b)):
                secs[m] = took
                if not ok:
                    out.append(m)
    finally:
        _restore_originals()
    return out, secs


def _site_said(seq: int, s: Site) -> str:
    return f"#{seq} {s.rel}:{s.line} [{s.kind}] {s.func}"


# Why `_run_mutant` sets a module aside for a mutant: it ran out with the
# file unmutated too, or it failed under the mutant and then passed alone
# with it, or failed (or ran out) alone without it.
RAN_OUT = "ran out unmutated too"
UNCONFIRMED = "failed, unconfirmed"


def _run_mutant(site: Site, sets: Sequence[Tuple[str, List[str]]],
                limits: Dict[str, Callable[[List[str]], float]],
                confirm: Sequence[Tuple[str, List[str]]] = (), root: Optional[str] = None
                ) -> Tuple[str, str, List[Tuple[str, str]]]:
    """(verdict, where, the modules set aside for it and why) for the mutant
    of `site`, applied in the tree `root` (a copy; this one unless given)
    and the file restored after. Each of `sets` runs its modules in their
    order (longest first) and stops at the first that fails or runs out, so
    `where` names the set and that module ("B: test_x"): the first of the
    set, in its order, to fail; a later one may fail too, unrun. The first
    set with one decides:
    - a module that fails is run alone twice more: with the mutant, where it
      must fail again, and with the file unparsed and unmutated (the
      control's rewrite), where it must pass. Then ('killed', where); else
      it is set aside for this mutant (UNCONFIRMED: a flaky module, or one
      that fails only after the modules before it, kills nothing) and the
      set goes on with the modules after it (TJ-4370: one such module no
      longer voids the run).
    - a module that runs out is run alone unmutated: passing, the mutant ran
      it out, ('timeout', where); failing, it is set aside (UNCONFIRMED);
      running out too, it is set aside (RAN_OUT, as the control sets aside a
      module that fails); the set goes on without it.
    ('unconfirmed', where) only when a set ran out before it named a module
    (none to set aside). When every set passes, the `confirm` sets (the
    modules the cuts left out) the same way: a kill there is ('cut_miss',
    where), a timeout ('cut_miss', where + ", timed out"). ('survived', '')
    when every module left passes. `limits` is keyed by a set's name's first
    word. CopyCrashed (a process killed from outside) propagates: the caller
    remakes the copy."""
    root = root or REPO
    path = os.path.join(root, site.rel)
    with open(os.path.join(REPO, site.rel), encoding="utf-8") as f:
        src = f.read()                  # this tree's file: a copy holds the same
    tree, fresh = find_sites(src, 1, len(src.splitlines()))
    kind, node = fresh[site.pos]
    mutate(tree, kind, node)
    try:
        mutated = ast.unparse(tree)
        compile(mutated, path, "exec")
    except (SyntaxError, ValueError, TypeError, RecursionError):
        return "compile_error", "", []
    plain = ast.unparse(ast.parse(src))
    aside: List[Tuple[str, str]] = []

    def put(text: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def judge(name: str, mods: List[str]) -> Optional[Tuple[str, str]]:
        """None when `mods` pass under the mutant; else (verdict, where)."""
        limit = limits[name.split()[0]]
        todo = list(mods)
        while todo:
            put(mutated)
            got, mod = _run_modules(todo, limit(todo), root)
            if got == "pass":
                return None
            where = f"{name}: {mod}"
            if mod not in todo:
                return "unconfirmed", where             # no module named: none set aside
            if got == "fail" and _run_modules([mod], limit([mod]), root)[0] != "fail":
                aside.append((mod, UNCONFIRMED))        # it passed alone, mutated
            else:
                put(plain)
                alone = _run_modules([mod], limit([mod]), root)[0]
                if alone == "pass":
                    return ("killed" if got == "fail" else "timeout"), where
                aside.append((mod, RAN_OUT if got == alone == "timeout" else UNCONFIRMED))
            todo = todo[todo.index(mod) + 1:]
        return None
    try:
        for name, mods in sets:
            got = judge(name, mods) if mods else None
            if got:
                return got[0], got[1], aside
        for name, mods in confirm:
            got = judge(name, mods) if mods else None
            if got:
                verdict, where = got
                if verdict == "timeout":
                    return "cut_miss", where + ", timed out", aside
                return ("cut_miss" if verdict == "killed" else verdict), where, aside
        return "survived", "", aside
    finally:
        put(src)


def _write_tallies(counts: Dict[str, int], missed: Sequence[str], unconfirmed: Sequence[str],
                   crashed: Sequence[str],
                   survivors: Sequence[Tuple[Site, List[str], List[str]]]) -> None:
    """The end of REPORT: the counts, each cut miss, unconfirmed mutant and
    crashed mutant, and the survivors (scripts/mutation_triage.py reads
    them), each flagged with the modules left out of the run that reach its
    line and those set aside for it (`_run_mutant`)."""
    with open(REPORT, "a") as rep:
        rep.write(f"\nkilled: {counts['killed']}\ntimeout: {counts['timeout']}\n"
                  f"cut misses (killed outside the cut: the trace missed the killer): "
                  f"{counts['cut_miss']}\n")
        for line in missed:
            rep.write(f"  {line}\n")
        rep.write(f"unconfirmed (a set ran out before it named a module: no record): "
                  f"{counts['unconfirmed']}\n")
        for line in unconfirmed:
            rep.write(f"  {line}\n")
        rep.write(f"crashed (the copy was killed from outside {RETRIES + 1} times: not "
                  f"counted, no record): {counts['crashed']}\n")
        for line in crashed:
            rep.write(f"  {line}\n")
        rep.write(f"compile_error(skipped): {counts['compile_error']}\n"
                  f"SURVIVED: {len(survivors)}\n")
        for s, reach, own in survivors:
            why = ([f"reached by {', '.join(reach)}, left out of the run"] if reach else []) \
                + ([f"{', '.join(own)} set aside for it"] if own else [])
            rep.write(f"  {s.rel}:{s.line} [{s.kind}]"
                      + (f" (flagged: {'; '.join(why)})" if why else "") + "\n")


class _Tally:
    """A run's counts and the report's mutant lines, tallied in the sample's
    order whatever order the copies end the mutants in (`_over_in_order`)."""

    def __init__(self) -> None:
        self.counts = {"killed": 0, "timeout": 0, "cut_miss": 0, "unconfirmed": 0,
                       "crashed": 0, "compile_error": 0}
        self.survived: List[int] = []
        self.lines: Dict[str, List[str]] = {"cut_miss": [], "unconfirmed": [], "crashed": []}
        # The modules set aside for a mutant (`_run_mutant`), and for each.
        self.aside: Set[str] = set()
        self.aside_for: Dict[int, Set[str]] = {}

    def add(self, seq: int, verdict: str, line: str) -> None:
        with open(REPORT, "a") as rep:
            rep.write(line + "\n")
        if verdict == "survived":
            self.survived.append(seq)
            return
        self.counts[verdict] += 1
        self.lines.get(verdict, []).append(line)


def _over_in_order(seqs: Sequence[int], copies: List[str],
                   one: Callable[[int, str], Tuple[str, str, List[Tuple[str, str]]]],
                   said: Callable[[int, str, str, Sequence[Tuple[str, str]]], str],
                   tally: _Tally) -> None:
    """Each of `seqs` by `one` across the copies (`_over_copies`), each said
    on stdout with its time as it ends and tallied in the order of `seqs`."""
    got: Dict[int, Tuple[str, str]] = {}
    order = list(seqs)

    def done(seq: int, result: Tuple[str, str, List[Tuple[str, str]]], seconds: float
             ) -> None:
        verdict, where, out = result
        tally.aside.update(m for m, _why in out)
        tally.aside_for.setdefault(seq, set()).update(m for m, _why in out)
        line = said(seq, verdict, where, out)
        print(f"{line} in {seconds:.1f}s", flush=True)
        got[seq] = verdict, line
        while order and order[0] in got:
            tally.add(order[0], *got.pop(order.pop(0)))
    _over_copies(seqs, copies, one, done)


def _why_no_record(why: str, extra: Sequence[int], ran: int, counts: Dict[str, int],
                   unflushed: Sequence[str], shard: Tuple[int, int] = (0, 1)) -> str:
    """Why a run's record is not printed ('' when it is). A part of a
    sample (`--shard K/N`) prints none (TJ-4430): a record is a whole run's,
    and `--jobs` runs one in parallel."""
    return (why or ("--site adds sites the seed did not draw" if extra else "")
            or (f"a part of the sample (--shard {shard[0] + 1}/{shard[1]}): only a whole "
                f"run is recorded" if shard[1] > 1 else "")
            or ("the trace is in no saved file" if not TRACE_MADE["sha256"] else "")
            or ("no mutant ran" if not ran else "")
            or (f"{counts['unconfirmed']} mutant(s) unconfirmed (a set ran out before it "
                f"named a module)" if counts["unconfirmed"] else "")
            or (f"{counts['crashed']} mutant(s) crashed" if counts["crashed"] else "")
            or (f"the trace lost {TRACE_LOSSES['lost']} process(es) that could not tell "
                f"their module" if TRACE_LOSSES["lost"] else "")
            or (f"{len(unflushed)} module(s) of A or B ended a process before its last "
                f"record in the trace, the first {unflushed[0]}" if unflushed else ""))


def _trace_said(jobs: int) -> Tuple[str, str]:
    """The report's lines on the trace a run used: its losses (TRACE_LOSSES),
    and the jobs it was made at and the control's (TJ-4373: their seconds are
    the machine's under that load, so a run is the same for any --jobs only
    for one saved trace)."""
    return (f"trace: {TRACE_LOSSES['lost']} process(es) could not tell their test module "
            f"(their lines are in no cut), {TRACE_LOSSES['unflushed']} ended before "
            f"their last record",
            f"trace {str(TRACE_MADE['sha256'])[:16] or '(unsaved)'} made at --jobs "
            f"{TRACE_MADE['jobs']}, the control run at --jobs {jobs} (the same report for "
            f"any --jobs needs the same saved trace)")


def parser_main(tid: str, n: int, seed: int, extra: Sequence[int] = (),
                shard: Tuple[int, int] = (0, 1), jobs: int = 1, retrace: bool = False,
                trace_file: Optional[str] = None, commit: Optional[str] = None,
                no_record: bool = False) -> None:
    """A run on one parser target: the sample (`stratified_sample`, plus the
    `extra` sites), its part `shard` (k, n), `jobs` copies of the tree
    (`make_copies`), the control in the first (`control_run`), then each
    mutant, `jobs` at once, one per copy (`_over_copies`), against sets A and
    B cut to the modules that ran its line, then set C (whole), without the
    modules the control set aside, each set's modules longest first by the
    trace; a set stops at its first failing module, and a kill counts once
    that module fails again alone with the mutant and passes alone without
    (`_run_mutant`; else that module is set aside for the mutant and the rest
    of the set runs); one that passes is confirmed against the rest of A (a
    kill there a cut miss), and, in a run with a cut miss, each survivor
    against the rest of B too. A survivor whose line a module left out of the
    whole run reaches, or for which a module was set aside, is flagged. Writes
    REPORT in the sample's order, the same for any `jobs` given one saved
    trace (its jobs and the control's are in it: TRACE_MADE;
    scripts/mutation_triage.py reads its SURVIVED lines), and prints a
    mutation-runs.json record when the tree is its commit's
    (`record_commit`), the sample is the seed's alone and whole (no `shard`
    part), the trace is saved (the record names its sha256), no set ran out
    before naming a module, no mutant crashed and the trace lost no line of a
    module of A or B; a tree that is not is refused unless `no_record` (a
    proof, printed without a record)."""
    k_of, n_parts = shard
    sha, tree_id, why = record_commit(commit)
    if why and not no_record:
        print(f"refusing to run: {why}. Record a run from a tree that is its commit's, "
              f"or run it with --no-record (a proof: no record is printed).")
        sys.exit(2)
    install_restore_handlers()
    rels = PARSER_TARGETS[tid]
    sites = parser_sites(rels)
    picked = sorted(set(stratified_sample(sites, n, seed)) | set(extra))
    print(f"{tid}: {len(sites)} sites; sample {n}, seed {seed}: {len(picked)} chosen")
    for i in picked:
        print("  " + _site_said(i, sites[i]))
    trace = load_trace(jobs, retrace, trace_file)
    losses, made = _trace_said(jobs)
    print(losses, flush=True)
    body = _body_lines(rels)
    a, b, c, failed = parser_sets(trace, body)
    print(f"sets: A {len(a)} module(s), B {len(b)}, C {len(c)}; "
          f"{len(failed)} failed in the trace, left out: {', '.join(failed) or 'none'}",
          flush=True)
    _check_imports_here()
    mine = picked[k_of::n_parts]

    def cut_of(mods: Sequence[str], seq: int) -> List[str]:
        return cut_to_line(mods, trace, sites[seq].rel, sites[seq].line, sites[seq].end_line,
                           body[sites[seq].rel])

    def longest(mods: Iterable[str]) -> List[str]:
        """`mods` longest first by the trace (the same order for any --jobs)."""
        return sorted(mods, key=lambda m: (-float(trace[m].get("seconds") or 0), m))
    cuts_a, cuts = ({seq: cut_of(mods, seq) for seq in mine} for mods in (a, b))
    used_b = [m for m in b if any(m in cut for cut in cuts.values())]
    copies = make_copies(max(1, jobs))
    _check_imports_here(copies[0])
    aside, secs = control_run(rels, a, used_b, c, jobs, copies[0])
    print(f"control (the target unparsed, unmutated, in a copy): A {len(a)} in "
          f"{secs['A']:.0f}s, C {len(c)} in {secs['C']:.0f}s, the cuts' {len(used_b)} B "
          f"module(s) alone; set aside: {', '.join(aside) or 'none'}", flush=True)
    a = [m for m in a if m not in aside]
    c = [m for m in c if m not in aside]
    # H3: a module of A or B whose trace lost a process's lines may be missing
    # from a cut; a process that could not tell its module may be any one's.
    unflushed = [m for m in a + b if int(trace[m].get("unflushed") or 0)]

    def took(m: str) -> float:
        return secs[m] if m in secs else float(trace[m].get("seconds") or 0)
    limits: Dict[str, Callable[[List[str]], float]] = {
        "A": lambda _m: max(TIMEOUT, 5 * secs["A"]),
        "B": lambda mods: max(TIMEOUT, 5 * sum(took(m) for m in mods)),
        "C": lambda _m: max(TIMEOUT, 5 * secs["C"])}
    tally = _Tally()
    counts, survived = tally.counts, tally.survived
    with open(REPORT, "w") as rep:
        rep.write(f"mutation run on {tid} (sample {n}, seed {seed}"
                  + (f", part {k_of + 1} of {n_parts}" if n_parts > 1 else "") + ")\n"
                  f"{losses}\n{made}\n"
                  f"left out (failed in the trace): {', '.join(failed) or 'none'}\n"
                  f"set aside (failed in the control): {', '.join(aside) or 'none'}\n")

    def said(seq: int, verdict: str, where: str, out: Sequence[Tuple[str, str]]) -> str:
        """A mutant's report line: the same for any --jobs (no time in it)."""
        word = {"cut_miss": "killed"}.get(verdict, verdict)
        cut_a = [m for m in cuts_a[seq] if m in a]
        cut = [m for m in cuts[seq] if m not in aside]
        return (f"{_site_said(seq, sites[seq])}: {word}{' by ' + where if where else ''} "
                f"(A: {len(cut_a)} of {len(a)}, B: {len(cut)} of {len(b)})"
                + "".join(f"; set aside for it ({why}): {', '.join(m for m, w in out if w == why)}"
                          for why in (RAN_OUT, UNCONFIRMED) if any(w == why for _m, w in out)))

    def first(seq: int, root: str) -> Tuple[str, str, List[Tuple[str, str]]]:
        cut_a = longest(m for m in cuts_a[seq] if m in a)
        cut = longest(m for m in cuts[seq] if m not in aside)
        return _run_mutant(sites[seq], (("A", cut_a), ("B", cut), ("C", longest(c))), limits,
                           (("A (outside its cut)", longest(m for m in a if m not in cut_a)),),
                           root)
    _over_in_order(mine, copies, first, said, tally)
    if counts["cut_miss"] and survived:
        # The trace missed a killer: the survivors' B cuts may miss one too.
        rest_b = [m for m in b if m not in used_b]
        more, more_secs = control_run(rels, [], rest_b, [], jobs, copies[0]) if rest_b \
            else ([], {})
        secs.update(more_secs)
        aside = aside + more
        note = (f"{counts['cut_miss']} cut miss(es): the {len(survived)} survivor(s) run "
                f"against the rest of B too (its {len(rest_b)} module(s) no cut held run "
                f"alone unmutated first; set aside: {', '.join(more) or 'none'})")
        print(note, flush=True)
        with open(REPORT, "a") as rep:
            rep.write(note + "\n")
        again, survived[:] = list(survived), []

        def second(seq: int, root: str) -> Tuple[str, str, List[Tuple[str, str]]]:
            cut = cuts[seq]
            return _run_mutant(sites[seq], (), limits, (
                ("B (outside its cut)",
                 longest(m for m in b if m not in cut and m not in aside)),), root)
        _over_in_order(again, copies, second, said, tally)
    _remove_copies()
    out_of_run = sorted(set(failed) | set(aside))
    # Flagged: a module out of the whole run reaches its line, or one was set
    # aside for it (TJ-4370), never one set aside for another mutant (TJ-4432).
    flags = {seq: (cut_of(out_of_run, seq), sorted(tally.aside_for.get(seq, set())))
             for seq in survived}
    flagged = [seq for seq in survived if flags[seq][0] or flags[seq][1]]
    _write_tallies(counts, tally.lines["cut_miss"], tally.lines["unconfirmed"],
                   tally.lines["crashed"], [(sites[seq], *flags[seq]) for seq in survived])
    ran = len(mine) - counts["compile_error"]
    whynot = _why_no_record(why, extra, ran, counts, unflushed, shard)
    if whynot or no_record:
        print(f"no record: {whynot or 'asked (--no-record)'}")
    else:
        record = {"parser": tid, "commit": sha, "tree": tree_id, "sites": len(sites), "n": n,
                  "seed": seed, "sampled": ran, "killed": counts["killed"],
                  "timeouts": counts["timeout"], "cut_misses": counts["cut_miss"],
                  "survived": len(survived), "equivalent": 0, "set_aside": out_of_run,
                  "set_aside_per_mutant": sorted(tally.aside),
                  "flagged": [f"{sites[q].rel}:{sites[q].line}" for q in flagged],
                  "lost": TRACE_LOSSES["lost"], "unflushed": TRACE_LOSSES["unflushed"],
                  "trace": TRACE_MADE["sha256"], "trace_jobs": TRACE_MADE["jobs"],
                  "control_jobs": jobs, "triage": ""}
        print("record (scripts/mutation-runs.json, after the triage moves the equivalent "
              "survivors to equivalent):", json.dumps(record))
    print("done — see", REPORT)


def main(shard=(0, 1)):
    """Every mutant of TARGETS, or (`shard` = (k, n)) the k-th of n
    interleaved parts of them, numbered over all targets in order, so n
    copies of the tree (one per part) run the whole set in parallel."""
    k_of, n_parts = shard
    install_restore_handlers()
    results = {"killed": 0, "timeout": 0, "survived": [],
               "compile_error": 0}
    t0 = time.time()
    with open(REPORT, "w") as rep:
        rep.write("mutation run started"
                  + (f" (part {k_of + 1} of {n_parts})" if n_parts > 1
                     else "") + "\n")
    baseline()

    total = 0
    seq = -1                    # every site's number over all targets
    for path, lo, hi in TARGETS:
        src = open(path).read()
        backup = src
        _ORIGINALS[path] = backup
        _, sites = find_sites(src, lo, hi)
        total += len(sites)
        for i, (kind, _) in enumerate(sites):
            seq += 1
            if seq % n_parts != k_of:
                continue
            # Re-parse fresh for each mutant, then locate the i-th
            # site again (node identity is per-parse).
            tree, fresh_sites = find_sites(src, lo, hi)
            k, node = fresh_sites[i]
            ln = node.lineno
            if kind == "unaryneg":
                # replace in parent — easiest via source line hack:
                # skip (rare, low value here)
                continue
            mutate(tree, k, node)
            try:
                mutated = ast.unparse(tree)
                compile(mutated, path, "exec")
            except Exception:
                results["compile_error"] += 1
                continue
            open(path, "w").write(mutated)
            try:
                if run_tests() == "survived":
                    results["survived"].append(
                        (os.path.relpath(path, REPO), ln, kind))
                else:
                    results["killed"] += 1
            except subprocess.TimeoutExpired:
                results["timeout"] += 1
            finally:
                open(path, "w").write(backup)
            done = (results["killed"] + results["timeout"]
                    + len(results["survived"])
                    + results["compile_error"])
            if done % 25 == 0:
                with open(REPORT, "a") as rep:
                    rep.write(f"progress: {done} mutants, "
                              f"{len(results['survived'])} survived, "
                              f"{time.time()-t0:.0f}s\n")
        open(path, "w").write(backup)
        _ORIGINALS.pop(path, None)

    with open(REPORT, "a") as rep:
        rep.write(f"\nDONE in {time.time()-t0:.0f}s\n")
        rep.write(f"killed: {results['killed']}\n")
        rep.write(f"timeout(=killed): {results['timeout']}\n")
        rep.write(f"compile_error(skipped): "
                  f"{results['compile_error']}\n")
        rep.write(f"SURVIVED: {len(results['survived'])}\n")
        for f, ln, kind in results["survived"]:
            rep.write(f"  {f}:{ln} [{kind}]\n")
    print("done — see", REPORT)


def _site_args(sites: Sequence[Site], specs: Sequence[str],
               error: Callable[[str], Any]) -> List[int]:
    """The indices into `sites` that `--site REL:LINE` names (every mutable
    site on that line); `error` for one that names none."""
    out: List[int] = []
    for spec in specs:
        rel, _, line = spec.rpartition(":")
        hit = [i for i, s in enumerate(sites) if s.rel == rel and str(s.line) == line]
        if not hit:
            error(f"--site {spec}: no mutable site on that line of the target")
        out += hit
    return out


def trace_report(tid: str, jobs: int = 1, retrace: bool = False,
                 trace_file: Optional[str] = None) -> None:
    """`--trace-only`: trace every test module (or reuse the saved trace),
    print the target's sets and the sites no module of them ran (a mutant
    there survives)."""
    rels = PARSER_TARGETS[tid]
    sites = parser_sites(rels)
    trace = load_trace(jobs, retrace, trace_file)
    body = _body_lines(rels)
    a, b, c, failed = parser_sets(trace, body)
    print(f"{tid}: sets A {len(a)} module(s), B {len(b)}, C {len(c)} (the trace made at "
          f"--jobs {TRACE_MADE['jobs']}); {len(failed)} failed in the trace: "
          f"{', '.join(failed) or 'none'}")
    for name, mods in (("A", a), ("B", b), ("C", c)):
        print(f"  {name}: {' '.join(mods) or '-'}")
    dark = [i for i, s in enumerate(sites)
            if not cut_to_line(a + b + c, trace, s.rel, s.line, s.end_line, body[s.rel])]
    print(f"{len(dark)} of {len(sites)} sites ran in no set's module")
    for i in dark:
        print("  " + _site_said(i, sites[i]))


def _list_targets() -> None:
    for path, lo, hi in TARGETS:
        print(f"{os.path.relpath(path, REPO)}:{lo}-{hi}")
    for tid, rels in PARSER_TARGETS.items():
        sites = parser_sites(rels)
        funcs = len({(s.rel, s.func) for s in sites})
        print(f"parser {tid}: {', '.join(rels)} ({len(sites)} sites in {funcs} functions)")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(
        description="Mutation-test the gains engine, or with --parser one parser. "
                    "REWRITES the target's files IN PLACE (backup/restore per mutant); "
                    "an interrupted run leaves a mutant applied — "
                    "restore with `git checkout -- src/taxjson/`.")
    ap.add_argument("--yes", action="store_true",
                    help="actually run (hours); required")
    ap.add_argument("--list-targets", action="store_true",
                    help="print the mutation regions and the parser targets, and exit")
    ap.add_argument("--shard", metavar="K/N",
                    help="run only part K of N (1-based) of the mutants, "
                         "for N copies of the tree in parallel; their "
                         "reports concatenate for scripts/mutation_triage.py "
                         "(a part prints no record: only a whole run is recorded)")
    ap.add_argument("--parser", metavar="ID", choices=sorted(PARSER_TARGETS),
                    help="mutate one parser target (a registry id) "
                         "instead of the engine")
    ap.add_argument("--sample", metavar="N", type=int, default=150,
                    help="with --parser: N sites, stratified by function; every "
                         "site when there are N or fewer (default %(default)s)")
    ap.add_argument("--seed", metavar="S", type=int, default=0,
                    help="with --parser: the sample's seed (default %(default)s)")
    ap.add_argument("--site", metavar="REL:LINE", action="append", default=[],
                    help="with --parser: add the sites on this line to the sample")
    ap.add_argument("--list-sites", action="store_true",
                    help="with --parser: print the chosen sites and exit")
    ap.add_argument("--jobs", metavar="J", type=int, default=1,
                    help="with --parser: mutants run at once, each in a copy of the tree "
                         "(the report is the same for any J, given one saved trace), and "
                         "test modules traced at once (default 1)")
    ap.add_argument("--trace-only", action="store_true",
                    help="with --parser: trace (or reuse the saved trace), print the "
                         "sets and the sites no set runs, and exit")
    ap.add_argument("--retrace", action="store_true",
                    help="with --parser: trace again, though a saved trace matches")
    ap.add_argument("--trace-file", metavar="PATH",
                    help="with --parser: where the trace of every test module is saved "
                         "(default: mutation_trace_all.json beside the report)")
    ap.add_argument("--commit", metavar="SHA",
                    help="with --parser: the commit the tree is of, for the record "
                         "(default: HEAD); the tree must be that commit's")
    ap.add_argument("--no-record", action="store_true",
                    help="with --parser: run on a tree that is not its commit's (a "
                         "proof); no record is printed")
    a = ap.parse_args()
    shard = (0, 1)
    if a.shard:
        try:
            k, n = (int(x) for x in a.shard.split("/"))
        except ValueError:
            ap.error(f"--shard {a.shard}: expected K/N, such as 2/6")
        if not 1 <= k <= n:
            ap.error(f"--shard {a.shard}: K must be 1 to N")
        shard = (k - 1, n)
    if a.sample < 1 or a.jobs < 1:
        ap.error("--sample and --jobs take a number of 1 or more")
    if a.list_targets:
        _list_targets()
        sys.exit(0)
    if a.parser:
        _sites = parser_sites(PARSER_TARGETS[a.parser])
        _extra = _site_args(_sites, a.site, ap.error)
        if a.trace_only:
            trace_report(a.parser, a.jobs, a.retrace, a.trace_file)
            sys.exit(0)
        if a.list_sites:
            _picked = sorted(set(stratified_sample(_sites, a.sample, a.seed)) | set(_extra))
            print(f"{a.parser}: {len(_sites)} sites; sample {a.sample}, seed {a.seed}: "
                  f"{len(_picked)} chosen")
            for _i in _picked:
                print("  " + _site_said(_i, _sites[_i]))
            sys.exit(0)
    elif a.site or a.list_sites or a.trace_only or a.commit or a.no_record:
        ap.error("--site, --list-sites, --trace-only, --commit and --no-record go with "
                 "--parser")
    if not a.yes:
        ap.error("refusing to run without --yes (this mutates the "
                 "working tree)")
    if a.parser:
        if a.commit and not re.fullmatch(r"[0-9a-f]{7,40}", a.commit):
            ap.error(f"--commit {a.commit}: expected a commit id")
        parser_main(a.parser, a.sample, a.seed, _extra, shard, a.jobs, a.retrace,
                    a.trace_file, a.commit, a.no_record)
    else:
        main(shard)
