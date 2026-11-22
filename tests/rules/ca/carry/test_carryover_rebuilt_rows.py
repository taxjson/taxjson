"""TJ-1554: in year folders a project reads only the prior year's lock, so the row
before it is rebuilt, and nothing capped what that row took from a T1A claim. By
hand: 2021 gain 100, 2022 loss 50, 2023 gain 20 (applied 20, back 30), a claim of 40:
the 10 left is for the 2024 loss of 30, so back 10, closing 20. Before, the rebuilt
2022 row used the whole claim, and 2024's lock carried 30 forward.

Synthetic data only: made-up tickers, amounts under a thousand.

Formerly: test_fix_tj1554_year_folders
"""
import itertools
import json
import re
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from _support.years_carryover_ledger import _TOML, _cli
from _support.years_carryover_folders import (_build_years, _nets, _ok, _row, _SALES,
                                              _trades)

from taxjson.bin.taxjson_carryover import build_canada_ledger


def _prior_only(nets, claimed, lock2023, opening=50.0):
    """What the 2024 project reads: 2023's lock alone."""
    back, applied, closing = lock2023
    return build_canada_ledger(nets, claimed, {2023: closing}, {2023: back},
                               {2023: applied}, locked_opening={2023: opening})


def _asked(nets, claimed, lock2023=None, opening=None, stated=None, year=2024, locks=None,
           carried_in=None, typed_in=False, typed=None):
    """The ledger `tjs carryover` builds for project `year`, with what it can't
    tell: `locks` {year: (back, applied, closing)}, 2023's alone by default;
    `opening`: 2023's, when taken from the record before it ({year: opening} with
    `locks`); `stated`: [carryover] carried_back; `carried_in`: the balance the
    estimate takes into `year`, `typed_in` when it is [estimate] other_losses
    (None: the tool run alone); `typed`: {lock year: its opening, when typed in}."""
    import argparse
    from taxjson.bin.taxjson_carryover import _canada_ledger
    locks = locks if locks is not None else ({2023: lock2023} if lock2023 else {})
    if not isinstance(opening, dict):
        opening = {} if opening is None else {2023: opening}
    args = argparse.Namespace(project_year=year, carried_in=carried_in,
                              carried_in_typed=typed_in, carried_back_year=[
                                  f"{y}={v!r}" for y, v in (stated or {}).items()])
    return _canada_ledger(args, nets, claimed, {y: k[2] for y, k in locks.items()},
                          {y: k[0] for y, k in locks.items()},
                          {y: k[1] for y, k in locks.items()}, None, opening, typed)


def _split(led, year=2024):
    return _row(led, year)["carried_back"], _row(led, year)["carryforward_balance"]


class TestTheLedger(unittest.TestCase):
    NETS = dict(y2021=100.0, y2022=-50.0, y2023=20.0, y2024=-30.0)

    @rule(R.CA_CARRY_04)
    def test_the_lock_s_opening_gives_the_rest_of_the_claim_back(self):
        for claimed in ({2021: 40.0}, {2021: 40.0, 2023: 20.0}):
            with self.subTest(claimed=claimed):
                led = _prior_only(_nets(**self.NETS), claimed, (30.0, 20.0, 0.0))
                self.assertEqual((_row(led, 2023)["carried_back"],
                                  _row(led, 2024)["carried_back"],
                                  _row(led, 2024)["carryforward_balance"]),
                                 (30.0, 10.0, 20.0))
                self.assertNotIn("unmatched_claims", led)

    @rule(R.CA_CARRY_04)
    def test_the_rebuilt_row_agrees_with_the_lock_after_it(self):
        led = _prior_only(_nets(**self.NETS), {2021: 40.0}, (30.0, 20.0, 0.0))
        r = _row(led, 2022)
        self.assertEqual((r["carried_back"], r["claimed_applied"],
                          r["carryforward_balance"]), (0.0, 0.0, 50.0))
        self.assertNotIn("carried_back_to", r)

    @rule(R.CA_CARRY_04)
    def test_a_lock_that_opened_lower_takes_nothing_back(self):
        """The 2022 return did carry 40 back: 2023 opened with 10."""
        led = _prior_only(_nets(**self.NETS), {2021: 40.0}, (0.0, 10.0, 0.0), opening=10.0)
        self.assertEqual(_row(led, 2022)["carried_back"], 40.0)
        self.assertEqual(_row(led, 2024)["carried_back"], 0.0)
        self.assertEqual(_row(led, 2024)["carryforward_balance"], 30.0)

    @rule(R.CA_CARRY_04)
    def test_it_gives_back_part_of_a_take(self):
        """2023 opened with 30: of the 40 the row took, 20 was the return's."""
        led = _prior_only(_nets(**self.NETS), {2021: 40.0}, (10.0, 20.0, 0.0), opening=30.0)
        r = _row(led, 2022)
        self.assertEqual((r["carried_back"], r["carried_back_to"],
                          r["carryforward_balance"]), (20.0, {"2021": 20.0}, 30.0))
        self.assertEqual(_row(led, 2024)["carried_back"], 10.0)

    @rule(R.CA_CARRY_04)
    def test_the_latest_take_goes_first_and_later_rows_rise_with_it(self):
        nets = _nets(y2019=100.0, y2020=100.0, y2021=-30.0, y2022=-30.0, y2023=20.0)
        led = build_canada_ledger(nets, {2019: 25.0, 2020: 25.0}, {2023: 40.0},
                                  {2023: 0.0}, {2023: 20.0}, locked_opening={2023: 60.0})
        self.assertEqual((_row(led, 2021)["carried_back"], _row(led, 2022)["carried_back"]),
                         (0.0, 0.0))
        self.assertEqual((_row(led, 2021)["carryforward_balance"],
                          _row(led, 2022)["carryforward_balance"]), (30.0, 60.0))
        led = build_canada_ledger(nets, {2019: 25.0, 2020: 25.0}, {2023: 30.0},
                                  {2023: 0.0}, {2023: 20.0}, locked_opening={2023: 50.0})
        self.assertEqual((_row(led, 2021)["carried_back"], _row(led, 2022)["carried_back"]),
                         (10.0, 0.0))
        self.assertEqual((_row(led, 2021)["carryforward_balance"],
                          _row(led, 2022)["carryforward_balance"]), (20.0, 50.0))

    @rule(R.CA_CARRY_04)
    def test_it_stops_at_the_lock_before_those_rows(self):
        """2021's lock recorded its 30: only the rows after it are rebuilt."""
        nets = _nets(y2020=100.0, y2021=-50.0, y2022=5.0, y2023=20.0)
        led = build_canada_ledger(nets, {2020: 60.0}, {2021: 20.0, 2023: 80.0},
                                  {2021: 30.0, 2023: 0.0}, {2021: 0.0, 2023: 20.0},
                                  locked_opening={2023: 100.0})
        self.assertEqual(_row(led, 2021)["carried_back"], 30.0)
        self.assertEqual(_row(led, 2021)["carryforward_balance"], 20.0)

    @rule(R.CA_CARRY_04)
    def test_a_later_year_s_loss_given_back_can_still_meet_the_claim(self):
        """A closed 2021's claim: 2022's loss is a later year's, whether the rebuilt
        2022 row's take of it stands or comes back."""
        nets = _nets(y2021=100.0, y2022=-50.0, y2023=20.0, y2024=5.0)
        led = build_canada_ledger(nets, {2021: 40.0}, {2021: 0.0, 2023: 30.0},
                                  {2021: 0.0, 2023: 0.0}, {2021: 0.0, 2023: 20.0},
                                  locked_opening={2023: 50.0})
        self.assertEqual(_row(led, 2023)["carried_back"], 0.0)
        self.assertEqual(_row(led, 2024)["carried_back"], 30.0)

    def test_no_recorded_opening_changes_nothing(self):
        led = build_canada_ledger(_nets(**self.NETS), {2021: 40.0}, {2023: 0.0},
                                  {2023: 0.0}, {2023: 10.0})
        self.assertEqual(_row(led, 2022)["carried_back"], 40.0)


class TestAPartialHistory(unittest.TestCase):
    """The books rebuild only part of what the lock opened with: it doesn't invent
    a balance, the lock's split stands, and the books still show as short."""
    @rule(R.CA_CARRY_04)
    def test_a_loss_the_books_don_t_hold(self):
        nets = _nets(y2021=100.0, y2022=-20.0, y2023=20.0, y2024=-30.0)
        led = _prior_only(nets, {2021: 40.0}, (30.0, 20.0, 0.0))
        r = _row(led, 2022)
        self.assertEqual(r["carryforward_balance"], 20.0)
        self.assertEqual(_row(led, 2023)["carried_back"], 30.0)
        self.assertEqual(_row(led, 2024)["carried_back"], 10.0)
        self.assertEqual(_row(led, 2024)["carryforward_balance"], 20.0)

    @rule(R.CA_CARRY_04)
    def test_no_books_before_the_lock(self):
        led = _prior_only(_nets(y2023=20.0, y2024=-30.0), {2021: 40.0}, (30.0, 20.0, 0.0))
        self.assertEqual(_row(led, 2023)["carried_back"], 30.0)
        self.assertEqual((_row(led, 2024)["carried_back"],
                          _row(led, 2024)["carryforward_balance"]), (10.0, 20.0))

    @rule(R.CA_CARRY_04)
    def test_the_books_are_still_said_to_be_short(self):
        nets = _nets(y2021=100.0, y2022=-20.0, y2023=0.0)
        led = _prior_only(nets, {}, (0.0, 0.0, 50.0))
        self.assertEqual(_row(led, 2023)["rebuilt_balance"], 20.0)
        self.assertEqual(_row(led, 2023)["carryforward_balance"], 50.0)


class TestWhatTheOpeningCantTell(unittest.TestCase):
    """The review of the first version (TJ-1574 to TJ-1581)."""
    @rule(R.CA_CARRY_04)
    def test_it_says_what_it_counted_as_unmet(self):
        """Books short of a 2022 loss that did meet the claim look just like a claim
        never met: the ledger takes the lock's word and says so (TJ-1574)."""
        nets = _nets(y2021=100.0, y2022=-50.0, y2023=0.0, y2024=-30.0)
        led = _prior_only(nets, {2021: 40.0}, (0.0, 0.0, 50.0))
        self.assertEqual(_row(led, 2023)["claims_given_back"], 40.0)
        self.assertEqual((_row(led, 2024)["carried_back"],
                          _row(led, 2024)["carryforward_balance"]), (40.0, 40.0))
        self.assertNotIn("claims_given_back", _row(led, 2022))

    def test_nothing_to_say_when_nothing_came_back(self):
        led = _prior_only(_nets(y2021=100.0, y2022=-20.0, y2023=0.0), {}, (0.0, 0.0, 50.0))
        self.assertNotIn("claims_given_back", _row(led, 2023))

    @rule(R.CA_CARRY_04)
    def test_cents_between_a_lock_and_its_rows_are_no_claim(self):
        """TJ-1576."""
        nets = _nets(y2021=100.0, y2022=-50.0, y2023=0.0)
        nets[2022]["dispositions"] = 8
        led = build_canada_ledger(nets, {2021: 40.0}, {2023: 10.03}, {2023: 0.0},
                                  {2023: 0.0}, locked_opening={2023: 10.03})
        self.assertEqual(_row(led, 2022)["carried_back"], 40.0)
        self.assertNotIn("unmatched_claims", led)
        self.assertNotIn("claims_given_back", _row(led, 2023))

    @rule(R.CA_CARRY_04)
    def test_a_lock_s_back_cents_under_the_claim_leave_nothing(self):
        """TJ-1577: the books hold 4 of the 8 sub-cent rows of 2022's 39.98; the
        lock took 39.98 of the claim of 40, and the 0.02 is the rows' cents."""
        nets = _nets(y2022=-30.0, y2023=20.0, y2024=-30.0)
        nets[2022]["dispositions"] = nets[2022]["subcent"] = 4
        led = build_canada_ledger(nets, {2021: 40.0}, {2023: 0.0}, {2023: 39.98},
                                  {2023: 20.0}, locked_opening={2023: 39.98})
        self.assertEqual(_row(led, 2022)["carried_back"], 0.0)
        self.assertEqual(_row(led, 2023)["carried_back"], 39.98)
        self.assertEqual(_row(led, 2024)["carried_back"], 0.0)
        self.assertEqual(_row(led, 2024)["carryforward_balance"], 30.0)

    @rule(R.CA_CARRY_04)
    def test_a_claimed_gain_isn_t_offered_for_a_t1a(self):
        """TJ-1578: the claim is on 2021's return, met by the lock or not."""
        nets = _nets(y2021=40.0, y2023=20.0, y2024=-30.0)
        led = _prior_only(nets, {2021: 40.0}, (30.0, 20.0, 0.0))
        self.assertEqual(_row(led, 2024)["carryback_candidates"], [])

    @rule(R.CA_CARRY_04)
    def test_the_rest_of_the_gain_is_offered_once(self):
        """TJ-1580: 60 of gain less the claim of 40; 20 of the 30 loss is left."""
        nets = _nets(y2021=60.0, y2023=20.0, y2024=-30.0)
        led = _prior_only(nets, {2021: 40.0}, (30.0, 20.0, 0.0))
        r = _row(led, 2024)
        self.assertEqual(r["carried_back"], 10.0)
        self.assertEqual(r["carryback_candidates"], [{"year": 2021, "amount": 20.0}])

    @rule(R.CA_CARRY_04)
    def test_a_lock_given_back_claim_isn_t_charged_twice(self):
        """TJ-1580, through a lock's excess: every lock read, the same offer."""
        nets = _nets(y2021=60.0, y2022=-30.0, y2023=-10.0, y2024=-30.0)
        led = build_canada_ledger(nets, {2021: 40.0}, {2023: 0.0}, {2023: 0.0}, {2023: 0.0})
        self.assertEqual(_row(led, 2024)["carried_back"], 10.0)
        self.assertEqual(_row(led, 2024)["carryback_candidates"],
                         [{"year": 2021, "amount": 20.0}])

    @rule(R.CA_CARRY_04)
    def test_the_refolded_rows_agree_with_themselves(self):
        """TJ-1580: not patched field by field: a row is what it would have been."""
        nets = _nets(y2021=100.0, y2022=-50.0, y2023=20.0)
        led = build_canada_ledger(nets, {2021: 40.0}, {2024: 30.0}, {2024: 0.0}, {2024: 0.0},
                                  locked_opening={2024: 50.0})
        r = _row(led, 2023)
        self.assertEqual((r["carryforward_balance"], r["available_to_apply"]), (50.0, 20.0))
        self.assertEqual(_row(led, 2022)["carryback_candidates"],
                         [{"year": 2021, "amount": 50.0}])

    @rule(R.CA_CARRY_04)
    def test_a_rebuilt_row_s_own_entry_isn_t_capped(self):
        """Only what it carried back comes back: 2022's own entry stands."""
        nets = _nets(y2021=100.0, y2022=-50.0, y2023=0.0)
        led = build_canada_ledger(nets, {2021: 40.0, 2022: 5.0}, {2023: 45.0}, {2023: 0.0},
                                  {2023: 0.0}, locked_opening={2023: 45.0})
        r = _row(led, 2022)
        self.assertEqual((r["carried_back"], r["claimed_applied"],
                          r["carryforward_balance"]), (0.0, 5.0, 45.0))

    @rule(R.CA_CARRY_04)
    def test_a_lock_s_back_uses_up_what_a_later_loss_can_give_a_closed_entry(self):
        """TJ-1578: 2023's loss went to 2020's claim and the lock's back of 60 also met
        30 of closed 2021's: nothing of a later loss is left for its rest of 20."""
        nets = _nets(y2020=100.0, y2021=100.0, y2023=-30.0, y2024=0.0)
        led = build_canada_ledger(nets, {2020: 30.0, 2021: 50.0}, {2021: 0.0, 2023: 20.0},
                                  {2021: 0.0, 2023: 60.0}, {2021: 0.0, 2023: 0.0})
        self.assertEqual(_row(led, 2023)["carried_back"], 60.0)
        self.assertEqual(_row(led, 2024)["carried_back"], 0.0)
        self.assertEqual(_row(led, 2024)["carryforward_balance"], 20.0)

    @rule(R.CA_CARRY_04)
    def test_the_project_year_s_row_doesn_t_echo_its_lock(self):
        """TJ-1579: a corrected CSV shrinks the 2024 loss to 5."""
        nets = _nets(y2021=100.0, y2024=-5.0)
        led = build_canada_ledger(nets, {2021: 10.0}, {2024: 20.0}, {2024: 10.0}, {2024: 0.0},
                                  own_year=2024)
        self.assertEqual(_row(led, 2024)["carried_back"], 5.0)


class TestTheSecondReview(unittest.TestCase):
    """TJ-1583 to TJ-1590."""
    @rule(R.CA_CARRY_04)
    def test_a_claim_given_back_twice_counts_once_and_a_lock_s_back_meets_it(self):
        """TJ-1583: the refold's uncapped 2023 row takes the 40 and gives it back
        again; the lock's own back of 40 then meets it, so nothing is counted."""
        nets = _nets(y2021=100.0, y2022=-50.0, y2023=5.0)
        led = build_canada_ledger(nets, {2021: 40.0}, {2024: 10.0}, {2024: 40.0},
                                  {2024: 0.0}, locked_opening={2024: 50.0})
        self.assertNotIn("claims_given_back", _row(led, 2024))
        self.assertEqual(_row(led, 2024)["carried_back"], 40.0)
        led = build_canada_ledger(nets, {2021: 40.0}, {2024: 50.0}, {2024: 0.0},
                                  {2024: 0.0}, locked_opening={2024: 50.0})
        self.assertEqual(_row(led, 2024)["claims_given_back"], 40.0)

    @rule(R.CA_CARRY_04)
    def test_only_what_waits_after_the_lock_s_back_is_counted(self):
        """TJ-1584: ordinary year folders, the lock's back met the claim."""
        nets = _nets(y2021=100.0, y2022=-50.0, y2023=0.0, y2024=-5.0)
        led = _prior_only(nets, {2021: 40.0}, (40.0, 0.0, 10.0))
        self.assertNotIn("claims_given_back", _row(led, 2023))
        self.assertEqual(_row(led, 2024)["carried_back"], 0.0)
        led = _prior_only(nets, {2021: 40.0}, (30.0, 0.0, 20.0))
        self.assertEqual(_row(led, 2023)["claims_given_back"], 10.0)

    @rule(R.CA_CARRY_04)
    def test_a_gain_year_s_rows_don_t_widen_the_cents_bound(self):
        """TJ-1585: 10,000 sales in 2021 leave the 40.00 gap a gap."""
        nets = _nets(**TestTheLedger.NETS)
        nets[2021]["dispositions"] = 10000
        led = _prior_only(nets, {2021: 40.0}, (30.0, 20.0, 0.0))
        self.assertEqual((_row(led, 2024)["carried_back"],
                          _row(led, 2024)["carryforward_balance"]), (10.0, 20.0))

    @rule(R.CA_CARRY_04)
    def test_the_rest_of_a_claim_survives_a_large_history(self):
        """TJ-1586."""
        nets = _nets(y2023=20.0, y2024=-30.0)
        nets[2023]["dispositions"] = 3000
        led = build_canada_ledger(nets, {2021: 40.0}, {2023: 0.0}, {2023: 30.0},
                                  {2023: 20.0}, locked_opening={2023: 59.98})
        self.assertEqual((_row(led, 2024)["carried_back"],
                          _row(led, 2024)["carryforward_balance"]), (10.0, 20.0))

    @rule(R.CA_CARRY_04)
    def test_the_bound_forgets_the_rows_behind_an_earlier_lock(self):
        """2019's 3,000 sub-cent rows are behind 2019's lock, not the balance the
        years after rebuild from it: a gap of 5 at 2022's lock is a gap. Closed
        2020's entry of 10 is met by 2021's loss of 5 in the books; 2022 opened
        with 55, so 2021's return didn't carry it back, and 2023's loss does."""
        nets = _nets(y2019=-50.0, y2020=100.0, y2021=-5.0, y2022=20.0, y2023=-30.0)
        nets[2019]["dispositions"] = nets[2019]["subcent"] = 3000
        led = build_canada_ledger(nets, {2020: 10.0}, {2019: 50.0, 2020: 50.0, 2022: 35.0},
                                  {2019: 0.0, 2020: 0.0, 2022: 0.0},
                                  {2019: 0.0, 2020: 0.0, 2022: 20.0},
                                  locked_opening={2022: 55.0})
        self.assertEqual(_row(led, 2021)["carried_back"], 0.0)
        self.assertEqual(_row(led, 2022)["claims_given_back"], 5.0)
        self.assertEqual((_row(led, 2023)["carried_back"],
                          _row(led, 2023)["carryforward_balance"]), (10.0, 55.0))

    @rule(R.CA_CARRY_04)
    def test_the_bound_counts_the_loss_rows_behind_the_balance(self):
        """...and a loss of many sub-cent rows is still a filed figure's cents away."""
        nets = _nets(y2021=100.0, y2022=-50.0, y2023=0.0)
        nets[2022]["dispositions"] = nets[2022]["subcent"] = 8
        led = build_canada_ledger(nets, {2021: 40.0}, {2023: 10.03}, {2023: 0.0},
                                  {2023: 0.0}, locked_opening={2023: 10.03})
        self.assertEqual(_row(led, 2022)["carried_back"], 40.0)
        self.assertNotIn("claims_given_back", _row(led, 2023))

    @rule(R.CA_CARRY_04)
    def test_a_capped_row_s_room_is_used_by_what_was_taken(self):
        """TJ-1588: two closed years' entries, each held to the later loss of 10,
        in a row allowed 25: the first's cap of 10 leaves room for the second."""
        nets = _nets(y2019=-30.0, y2020=100.0, y2021=100.0, y2022=-10.0)
        led = build_canada_ledger(nets, {2020: 50.0, 2021: 20.0},
                                  {2020: 30.0, 2021: 30.0}, {2020: 0.0, 2021: 0.0},
                                  {2020: 0.0, 2021: 0.0}, back_caps={2022: 25.0})
        r = _row(led, 2022)
        self.assertEqual(r["carried_back"], 20.0)
        self.assertEqual(r["carried_back_to"], {"2020": 10.0, "2021": 10.0})


class TestTheRenderedLines(unittest.TestCase):
    """TJ-1589, TJ-1590: the text beside the figures."""
    def _render(self, led, details=False):
        from taxjson.bin.taxjson_carryover import render_brief, render
        return " ".join((render(led, "canada", None, True) if details
                         else render_brief(led, "canada", None)).split())

    def test_a_short_history_keeps_both_lines(self):
        nets = _nets(y2021=100.0, y2022=-20.0, y2023=0.0)
        led = _prior_only(nets, {2021: 40.0}, (0.0, 0.0, 50.0))
        text = self._render(led)
        self.assertIn("its lock carried out 50.00; these books, 20.00", text)

    def test_the_balance_it_can_t_tell_isn_t_printed_as_one(self):
        """TJ-1574: the figure that rests on what a rebuilt year carried back is
        working, in both views; the ways out name the key and the year."""
        nets = _nets(y2021=100.0, y2022=-50.0, y2023=0.0, y2024=-30.0)
        led = _asked(nets, {2021: 40.0}, (0.0, 0.0, 50.0), opening=50.0)
        for details in (False, True):
            text = self._render(led, details)
            self.assertIn("carryforward after 2024: can't tell (80.00 if 2022 carried "
                          "back what these books show, 40.00 if nothing)", text)
            self.assertIn("T1A carry-back of 2024's loss: can't tell (0.00 if 2022 carried "
                          "back what these books show, 40.00 if nothing)", text)
        self.assertIn("add `carried_back = { 2022 = 40.00 }` if it did, as the books read "
                      "(this year then carries back 0.00), or `carried_back = { 2022 = 0 }` "
                      "if not (then 40.00); or put its close-year lock in filed/", text)
        self.assertIn("its folder made again (its year, prior_year_record and the shared "
                      "inputs)", text)
        self.assertIn("! can't tell what 2022 carried back (T1A): set [carryover] "
                      "carried_back", self._render(led))
        settled = _asked(nets, {2021: 40.0}, (0.0, 0.0, 10.0), opening=10.0)
        self.assertIn("carryforward after 2024: 40.00", self._render(settled))

    def test_an_answer_is_shown_as_the_person_s(self):
        nets = _nets(y2021=100.0, y2022=-50.0, y2023=0.0, y2024=-30.0)
        led = _asked(nets, {2021: 40.0}, (0.0, 0.0, 50.0), opening=50.0,
                     stated={2022: 40.0})
        for details in (False, True):
            text = self._render(led, details)
            self.assertIn("2022: carried back 40.00, your figure ([carryover] "
                          "carried_back), not these books'", text)
            self.assertIn("carryforward after 2024: 80.00", text)

    def test_an_expired_claim_isn_t_promised_a_later_loss(self):
        nets = _nets(y2019=100.0, y2022=-50.0, y2023=0.0)
        led = _prior_only(nets, {2019: 40.0}, (0.0, 0.0, 50.0))
        self.assertEqual(led["expired_claims"], {"2019": 40.0})
        text = self._render(led, details=True)
        self.assertIn("claimed for 2019 was never met by a loss", text)
        self.assertNotIn("a later loss can meet it", text)


class TestWhichOpeningIsRead(unittest.TestCase):
    """End to end: only a figure taken from the record before it."""
    def _project(self, top):
        _build_years(top, {2024: "2021 = 40.00"},
                     {**_SALES, 2024: ("ZZQD", 470.0)}, close=(2021, 2022, 2023))
        return top / "2024"

    @rule(R.CA_CARRY_04)
    def test_the_own_year_s_lock_doesn_t_move_the_split_it_recorded(self):
        """TJ-1575: the same split before and after the close, with the opening a hand
        figure in a project with no record before it."""
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            sales = {2021: ("ZZQA", 600.0), 2022: ("ZZQB", 450.0), 2024: ("ZZQD", 470.0)}
            _build_years(top, {}, sales)
            y = top / "2024"
            toml = (y / "taxjson.toml").read_text()
            toml = re.sub(r'prior_year_record = .*\n', "", toml).replace(
                "[estimate]\n", "[estimate]\nother_losses = 50.00\n"
            ) + '\n[carryover]\nclaimed = { 2021 = 40.00 }\n'
            (y / "taxjson.toml").write_text(toml)
            _ok(_cli(y, "run", "--no-input"))
            # No lock says what 2022's return carried back: the ledger asks
            # (TJ-1574's review), and is told it was the 40.
            asked = json.loads(_ok(_cli(y, "carryover", "--json", "--on-books")).stdout)
            self.assertEqual(asked["carry_back_cant_tell"]["years"], [2022])
            (y / "taxjson.toml").write_text(toml + "carried_back = { 2022 = 40.00 }\n")
            before = _row(json.loads(_ok(_cli(y, "carryover", "--json", "--on-books")).stdout), 2024)
            _ok(_cli(y, "close-year", "--yes"))
            after = _row(json.loads(_ok(_cli(y, "carryover", "--json")).stdout), 2024)
            self.assertEqual(before["carried_back"], after["carried_back"])
            self.assertNotIn("claims_given_back", after)

    @rule(R.CA_CARRY_04)
    def test_a_hand_opening_is_no_evidence(self):
        """TJ-1574: a 2023 opening typed into [estimate] other_losses, not taken from
        2022's record, may hold losses from before these books."""
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            self._project(top)
            lock = top / "2023" / "filed" / "2023.json"
            doc = json.loads(lock.read_text())
            self.assertIn("close-year record)",
                          doc["carryforwards"]["net_capital_loss"]["opening_source"])
            from taxjson.bin.taxjson_carryover import lock_figure
            self.assertEqual(lock_figure(lock, "canada")["opening"], 50.0)
            doc["carryforwards"]["net_capital_loss"]["opening_source"] = (
                "[estimate] other_losses")
            lock.write_text(json.dumps(doc))
            self.assertNotIn("opening", lock_figure(lock, "canada"))

    @rule(R.CA_CARRY_04)
    def test_a_stale_opening_says_so_and_gives_nothing_back(self):
        """TJ-1587: 2022 closed again after 2023 was; 2024 reads 2023's lock, and
        2022's behind it (TJ-1574): its row is that record, as closed again."""
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            self._project(top)
            rec = top / "2022" / "filed" / "2022.json"
            doc = json.loads(rec.read_text())
            doc["carryforwards"]["net_capital_loss"]["closing"] = 10.0
            rec.write_text(json.dumps(doc))
            y = top / "2024"
            res = _ok(_cli(y, "carryover", "--json"))
            led = json.loads(res.stdout)
            self.assertEqual(_row(led, 2023)["opening_stale"],
                             {"opening": 50.0, "record_now": 10.0})
            self.assertNotIn("claims_given_back", _row(led, 2023))
            self.assertNotIn("carry_back_cant_tell", led)
            self.assertEqual((_row(led, 2022)["carried_back"],
                              _row(led, 2022)["carryforward_balance"]), (0.0, 10.0))
            self.assertIn("not closed again", " ".join(res.stderr.split()))


class TestAStaleLockStopsTheClose(unittest.TestCase):
    """TJ-4883 (round 3's scenario): 2022 is closed again with --force after 2023
    was, its claim now in its taxjson.toml, so 2022 carries 10 out, not 50. 2023's
    lock still opened with 50: the balance 2024 takes from it is stale. Only `tjs
    carryover` said so, and 2024 closed at 60 forward."""
    @rule(R.CA_CARRY_04)
    def test_the_estimate_says_unknown_and_the_close_refuses_until_reclosed(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            _build_years(top, {2024: "2021 = 40.00"},
                         {**_SALES, 2024: ("ZZQD", 470.0)}, close=(2021, 2022, 2023))
            d22, d23, y = top / "2022", top / "2023", top / "2024"
            toml = d22 / "taxjson.toml"
            toml.write_text(toml.read_text() + "\n[carryover]\nclaimed = { 2021 = 40.00 }\n")
            _ok(_cli(d22, "close-year", "--yes", "--force"))
            ncl = json.loads((d22 / "filed" / "2022.json").read_text()
                             )["carryforwards"]["net_capital_loss"]
            self.assertEqual((ncl["carried_back"], ncl["closing"]), (40.0, 10.0))
            why = ("2023's close-year lock opened with 50.00, but the record it took "
                   "that from now carries 10.00: 2023 was not closed again after that "
                   "record changed. Close 2023 again first (`tjs close-year --force` "
                   "in 2023's project)")
            res = _ok(_cli(y, "estimate", "--json"))
            est = json.loads(res.stdout)["estimate"]
            self.assertIn(why, est["carry_back_unknown"])
            self.assertNotIn("losses_carried_forward", est)
            self.assertIn("the net capital loss carried in is a stale lock's",
                          " ".join(res.stderr.split()))
            text = " ".join(_ok(_cli(y, "estimate")).stdout.split())
            self.assertIn("the carry-forward is unknown) The lock the loss carried in "
                          "came from is stale", text)
            closed = _cli(y, "close-year", "--yes")
            self.assertEqual(closed.returncode, 1, closed.stdout + closed.stderr)
            said = " ".join((closed.stdout + closed.stderr).split())
            self.assertIn("comes from a close-year lock that is stale", said)
            self.assertIn(why, said)
            self.assertIn("Nothing was written", said)
            self.assertFalse((y / "filed" / "2024.json").exists())
            # The fix it names: 2023 closed again, on what 2022 carries now.
            _ok(_cli(d23, "close-year", "--yes", "--force"))
            est = json.loads(_ok(_cli(y, "estimate", "--json")).stdout)["estimate"]
            self.assertNotIn("carry_back_unknown", est)
            _ok(_cli(y, "close-year", "--yes"))
            ncl = json.loads((y / "filed" / "2024.json").read_text()
                             )["carryforwards"]["net_capital_loss"]
            self.assertEqual((ncl["opening"], ncl.get("carried_back", 0.0), ncl["closing"]),
                             (0.0, 0.0, 30.0))

    def test_a_typed_figure_or_a_lock_in_step_is_not_stale(self):
        from taxjson.cli.common import _stale_opening
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            _build_years(top, {}, _SALES, close=(2021, 2022))
            y = top / "2023"
            label = "../2022/filed/2022.json (the 2022 close-year record)"
            self.assertIsNone(_stale_opening(y, label))
            for other in (None, "none", "[estimate] other_losses", "--other-losses",
                          "../2020/filed/2020.json (the 2020 close-year record)"):
                self.assertIsNone(_stale_opening(y, other), other)
            rec = top / "2021" / "filed" / "2021.json"
            doc = json.loads(rec.read_text())
            doc["carryforwards"]["net_capital_loss"]["closing"] = 7.0
            rec.write_text(json.dumps(doc))
            self.assertIn("2022's close-year lock opened with 0.00, but the record it "
                          "took that from now carries 7.00", _stale_opening(y, label))


class TestWhatTheLedgerCantTell(unittest.TestCase):
    """TJ-1574 and its review: a rebuilt year's row met a claim, and nothing says
    its return did. The ledger says so when the project year's figures rest on
    it, [carryover] carried_back answers it, and only there."""
    STD = dict(y2021=100.0, y2022=-50.0, y2023=20.0, y2024=-30.0)

    @rule(R.CA_CARRY_04)
    def test_a_first_lock_with_no_record_behind_it(self):
        """The review's P1: only 2023 is closed (its opening typed in, or none), a
        claim of 30 under 2021 added in 2024. The rebuilt 2022 row met it: back 0,
        60 forward, with no word; if 2022's return carried back nothing, 30 / 30."""
        for lock in ((0.0, 20.0, 30.0), (0.0, 0.0, 0.0)):
            led = _asked(_nets(**self.STD), {2021: 30.0}, lock)
            ct = led["carry_back_cant_tell"]
            self.assertEqual(ct["years"], [2022])
            self.assertEqual((ct["if_none"]["carried_back"], ct["if_all"]["carried_back"]),
                             (30.0, 0.0))
            self.assertEqual(ct["if_none"]["carried_back_to"], {"2021": 30.0})
            none = _asked(_nets(**self.STD), {2021: 30.0}, lock, stated={2022: 0.0})
            every = _asked(_nets(**self.STD), {2021: 30.0}, lock, stated={2022: 30.0})
            for answered, side in ((none, "if_none"), (every, "if_all")):
                self.assertNotIn("carry_back_cant_tell", answered)
                self.assertEqual(_split(answered), (ct[side]["carried_back"],
                                                    ct[side]["carryforward"]))
            self.assertEqual(_row(none, 2022)["carried_back_stated"], 0.0)
            self.assertEqual(_split(none, 2022), (0.0, 50.0))
        self.assertEqual((ct["if_none"]["carryforward"], ct["if_all"]["carryforward"]),
                         (0.0, 30.0))

    @rule(R.CA_CARRY_04)
    def test_a_lock_that_opened_above_the_rebuilt_rows(self):
        """TJ-1574's own shape, answered both ways; 40 said of a loss of which the
        books hold 25 is still 40 met, as a lock's carry-back is."""
        nets = _nets(y2021=100.0, y2022=-50.0, y2023=0.0, y2024=-30.0)
        lock = (0.0, 0.0, 50.0)
        led = _asked(nets, {2021: 40.0}, lock, opening=50.0)
        self.assertEqual(led["carry_back_cant_tell"]["years"], [2022])
        self.assertEqual(_split(_asked(nets, {2021: 40.0}, lock, 50.0, {2022: 0.0})),
                         (40.0, 40.0))
        met = _asked(nets, {2021: 40.0}, lock, 50.0, {2022: 40.0})
        self.assertEqual(_split(met), (0.0, 80.0))
        self.assertNotIn("claims_given_back", _row(met, 2023))
        short = _asked(_nets(y2021=100.0, y2022=-25.0, y2023=0.0, y2024=-30.0),
                       {2021: 40.0}, lock, 50.0, {2022: 40.0})
        self.assertEqual((_split(short, 2022), _split(short)), ((40.0, 0.0), (0.0, 80.0)))
        self.assertNotIn("unmatched_claims", short)

    @rule(R.CA_CARRY_04)
    def test_no_lock_at_all_and_rebuilt_years_after_the_last_lock(self):
        nets = _nets(y2021=100.0, y2022=-50.0, y2024=-30.0)
        led = _asked(nets, {2021: 30.0})
        self.assertEqual(led["carry_back_cant_tell"]["years"], [2022])
        self.assertEqual(_split(_asked(nets, {2021: 30.0}, stated={2022: 0.0})), (30.0, 50.0))
        nets = _nets(y2020=100.0, y2021=0.0, y2022=-50.0, y2023=-5.0, y2024=-30.0)
        led = _asked(nets, {2021: 30.0}, year=2024, locks={2021: (0.0, 0.0, 0.0)})
        self.assertEqual(led["carry_back_cant_tell"]["years"], [2022, 2023])
        # ...but a claim 2024 can't reach moves only the rebuilt balance, which
        # no command carries or locks: nothing is asked (TJ-4884).
        nets = _nets(y2020=100.0, y2021=0.0, y2022=-50.0, y2024=-30.0)
        led = _asked(nets, {2020: 30.0}, year=2024, locks={2021: (0.0, 0.0, 0.0)})
        self.assertNotIn("carry_back_cant_tell", led)

    @rule(R.CA_CARRY_04)
    def test_a_balance_the_person_gave_settles_what_it_can(self):
        """TJ-4884 and TJ-4885. The release check's history: books from 2022
        (+100, a loss of 100 in 2023, +50, +30), `claimed = { 2022 = 40 }`, the
        first lock 2024's, opened from [estimate] other_losses = 60. That balance
        sets a floor (a loss from before the books may be in it): 60 left of
        2023's 100 says 2023 carried back at least 40, and the claim it could meet
        is 40, so nothing is asked of 2024, or of 2025 after it, gain or loss."""
        from taxjson.bin.taxjson_carryover import render, render_brief
        nets = _nets(y2022=100.0, y2023=-100.0, y2024=50.0, y2025=30.0)
        lost = _nets(y2022=100.0, y2023=-100.0, y2024=50.0, y2025=-30.0)
        lock = {2024: (0.0, 50.0, 10.0)}
        cases = ((nets, dict(year=2024, carried_in=60.0, typed_in=True)),
                 (nets, dict(year=2025, locks=lock, typed={2024: 60.0}, carried_in=10.0)),
                 (lost, dict(year=2025, locks=lock, typed={2024: 60.0}, carried_in=10.0)))
        for books, case in cases:
            with self.subTest(case=case):
                led = _asked(books, {2022: 40.0}, **case)
                self.assertNotIn("carry_back_cant_tell", led)
                self.assertEqual(_row(led, 2023)["carried_back"], 40.0)
                self.assertEqual(_row(led, 2023)["carried_back_inferred"],
                                 {"carried_in": 60.0, "into": 2024, "at_least": 40.0,
                                  "at_most": 40.0, "books_leave": 100.0, "years": [2023],
                                  "ceiling": "claims", "basis": "books_net"})
                self.assertEqual(_row(led, case["year"])["carried_back"], 0.0)
                for text in (render_brief(led, "CAD", None), render(led, "CAD", None, True)):
                    self.assertNotIn("can't tell", text)
                    self.assertIn("2023: carried back at least 40.00 (these books leave "
                                  "100.00, you carried 60.00 into 2024: [estimate] "
                                  "other_losses), at most 40.00 (the [carryover] claimed "
                                  "amounts in reach); from the books' nets: a T3 or FX "
                                  "gain on that return changes it, and [carryover] "
                                  "carried_back overrides it", " ".join(text.split()))

    @rule(R.CA_CARRY_04)
    def test_with_no_balance_given_the_estimate_s_loss_left_decides(self):
        """The same history with nothing typed (the 2024 lock opened from nothing):
        a gain year with nothing left to split is not asked; a loss year is."""
        nets = _nets(y2022=100.0, y2023=-100.0, y2024=50.0, y2025=30.0)
        lock = {2024: (0.0, 0.0, 0.0)}
        for case in (dict(year=2024, carried_in=0.0),
                     dict(year=2025, locks=lock, carried_in=0.0)):
            self.assertNotIn("carry_back_cant_tell", _asked(nets, {2022: 40.0}, **case), case)
        lost = _asked(_nets(y2022=100.0, y2023=-100.0, y2024=50.0, y2025=-30.0),
                      {2022: 40.0}, year=2025, locks=lock, carried_in=0.0)
        ct = lost["carry_back_cant_tell"]
        self.assertEqual((ct["years"], ct["if_all"]["carried_back"],
                          ct["if_none"]["carried_back"], ct["if_none"]["loss_left"]),
                         ([2023], 0.0, 30.0, 30.0))

    @rule(R.CA_CARRY_04)
    def test_a_gain_year_with_a_loss_left_is_split_on_the_balance_given(self):
        """TJ-4885, the check's P1: 2023 -100, `claimed = { 2022 = 40 }`, no 2023
        lock, 2024 +80. The estimate asks whenever a loss is left, and a balance
        carried in can carry back in a gain year (TJ-1454), so "a gain year is
        never asked" closed 2024 at 0 / 20 whatever the facts. After a lock (2022's,
        carrying nothing out) the balance typed in says which reading holds: 100
        carried in, 2023 carried back nothing and 2024's row takes the 40 (the
        estimate has 20 left: 20 back, 0 forward); 60, 2023 carried back the 40."""
        nets = _nets(y2022=100.0, y2023=-100.0, y2024=80.0)
        lock = {2022: (0.0, 0.0, 0.0)}
        for carried_in, back23, back24 in ((100.0, 0.0, 40.0), (60.0, 40.0, 0.0),
                                           (70.0, 30.0, 10.0)):
            with self.subTest(carried_in=carried_in):
                led = _asked(nets, {2022: 40.0}, locks=lock, carried_in=carried_in,
                             typed_in=True)
                self.assertNotIn("carry_back_cant_tell", led)
                self.assertEqual((_row(led, 2023)["carried_back"],
                                  _row(led, 2024)["carried_back"]), (back23, back24))
                self.assertEqual(_row(led, 2023)["carried_back_inferred"]["at_least"],
                                 _row(led, 2023)["carried_back_inferred"]["at_most"])
        self.assertNotIn("carry_back_cant_tell",
                         _asked(nets, {2022: 40.0}, locks=lock, carried_in=0.0))
        # The entry the balance answers: the same is a note, another is refused.
        code, _led, said = self._said(nets, {2022: 40.0}, locks=lock, carried_in=100.0,
                                      typed_in=True, stated={2023: 0.0})
        self.assertIsNone(code)
        self.assertIn("2023 is no longer needed: the 100.00 carried into 2024 ([estimate] "
                      "other_losses) says the same", said)
        code, _led, said = self._said(nets, {2022: 40.0}, locks=lock, carried_in=100.0,
                                      typed_in=True, stated={2023: 40.0})
        self.assertEqual(code, 1)
        self.assertIn("says 2023 carried back 40.00, but the 100.00 carried into 2024 "
                      "([estimate] other_losses) says 0.00", said)

    @rule(R.CA_CARRY_04)
    def test_with_no_lock_before_the_balance_is_a_floor(self):
        """The ruling of 2026-11-21: with no lock before the unchecked years, a loss
        from before the books may be in the balance typed in, so it says only the
        least those years carried back: what the books leave, less the balance.
        100 typed after a loss of 100: at least 0, at most the claim's 40: asked
        (it closed at 20 / 0 on "the books are the whole history"). 60: at least 40,
        at most 40: settled. An entry that leaves less than the balance is taken,
        the rest being from before the books; one that leaves more is refused."""
        from taxjson.bin.taxjson_carryover import render_brief
        nets = _nets(y2022=100.0, y2023=-100.0, y2024=80.0)
        ask = _asked(nets, {2022: 40.0}, carried_in=100.0, typed_in=True)
        ct = ask["carry_back_cant_tell"]
        self.assertEqual((ct["years"], ct["least"], ct["books"]),
                         ([2023], {"2023": 0.0}, {"2023": 40.0}))
        self.assertEqual((ct["if_none"]["carried_back"], ct["if_all"]["carried_back"],
                          ct["if_none"]["loss_left"]), (20.0, 0.0, 20.0))
        settled = _asked(nets, {2022: 40.0}, carried_in=60.0, typed_in=True)
        self.assertNotIn("carry_back_cant_tell", settled)
        self.assertEqual(_row(settled, 2023)["carried_back_inferred"],
                         {"carried_in": 60.0, "into": 2024, "at_least": 40.0,
                          "at_most": 40.0, "books_leave": 100.0, "years": [2023],
                          "ceiling": "claims", "basis": "books_net"})
        none = _asked(nets, {2022: 40.0}, carried_in=100.0, typed_in=True,
                      stated={2023: 0.0})
        self.assertNotIn("carried_in_before_books", none)
        self.assertEqual(_row(none, 2024)["carried_back"], 40.0)    # (20 left: 20 back)
        took = _asked(nets, {2022: 40.0}, carried_in=100.0, typed_in=True,
                      stated={2023: 40.0})
        self.assertNotIn("carry_back_cant_tell", took)
        self.assertEqual(took["carried_in_before_books"],
                         [{"into": 2024, "carried_in": 100.0, "leaves": 60.0,
                           "before_books": 40.0}])
        self.assertIn("Your [carryover] carried_back entries leave 60.00 carried into "
                      "2024; the 100.00 you carried in holds 40.00 from before these "
                      "books", " ".join(render_brief(took, "CAD", None).split()))
        # A floor short of the ceiling: 30 typed after a loss of 50, a claim of 40.
        nets = _nets(y2021=100.0, y2022=20.0, y2023=-50.0, y2024=-30.0)
        ask = _asked(nets, {2021: 40.0}, carried_in=30.0, typed_in=True)
        self.assertEqual((ask["carry_back_cant_tell"]["least"],
                          ask["carry_back_cant_tell"]["books"]),
                         ({"2023": 20.0}, {"2023": 40.0}))
        code, _led, said = self._said(nets, {2021: 40.0}, carried_in=30.0, typed_in=True,
                                      stated={2023: 10.0})
        self.assertEqual(code, 1)
        self.assertIn("[carryover] carried_back (2023 = 10.00) leaves 40.00 carried into "
                      "2024, but [estimate] other_losses says 30.00, less than that", said)
        # The ruling's own cases: 10 typed settles it at 40; 50 typed asks.
        self.assertNotIn("carry_back_cant_tell",
                         _asked(nets, {2021: 40.0}, carried_in=10.0, typed_in=True))
        self.assertIn("carry_back_cant_tell",
                      _asked(nets, {2021: 40.0}, carried_in=50.0, typed_in=True))

    @rule(R.CA_CARRY_04)
    def test_the_inference_line_says_what_it_rests_on(self):
        """Round 5. Two unchecked years that share a forced total (losses of 50
        and 40, claims of 60, 30 typed in: at least 60, at most 60) are shown, as a
        pair. After a lock the balance is the ceiling too, and is named as such.
        Either way the figure is from the books' nets (`basis`): a T3 or FX gain
        on that return changes it, and the line says so."""
        from taxjson.bin.taxjson_carryover import render_brief
        nets = _nets(y2020=100.0, y2021=-50.0, y2022=-40.0, y2024=-10.0)
        led = _asked(nets, {2020: 60.0}, carried_in=30.0, typed_in=True)
        self.assertNotIn("carry_back_cant_tell", led)
        self.assertEqual((_row(led, 2021)["carried_back"], _row(led, 2022)["carried_back"]),
                         (50.0, 10.0))
        for year in (2021, 2022):
            inf = _row(led, year)["carried_back_inferred"]
            self.assertEqual((inf["years"], inf["at_least"], inf["at_most"], inf["basis"]),
                             ([2021, 2022], 60.0, 60.0, "books_net"))
        text = " ".join(render_brief(led, "CAD", None).split())
        self.assertEqual(text.count("carried back at least"), 1)
        self.assertIn("2021 and 2022: carried back at least 60.00 in all (these books "
                      "leave 90.00, you carried 30.00 into 2024: [estimate] other_losses), "
                      "at most 60.00 (the [carryover] claimed amounts in reach); from the "
                      "books' nets: a T3 or FX gain on that return changes it, and "
                      "[carryover] carried_back overrides it", text)
        nets = _nets(y2022=100.0, y2023=-100.0, y2024=80.0)
        led = _asked(nets, {2022: 40.0}, locks={2022: (0.0, 0.0, 0.0)}, carried_in=100.0,
                     typed_in=True)
        self.assertEqual(_row(led, 2023)["carried_back_inferred"]["ceiling"], "balance")
        self.assertIn("at most 0.00 (that balance again, after the lock before)",
                      " ".join(render_brief(led, "CAD", None).split()))

    @rule(R.CA_CARRY_04)
    def test_a_gain_year_between_lowers_the_floor(self):
        """A gain year between (2023 +20, no `claimed` entry: what it applied is the
        taxpayer's choice, nothing to 20): other_losses = 30 after a 2022 loss of 50
        says 2022 carried back at least 0. Asked, with a loss in 2024 (30 or 0) and
        with a gain of 20 there (the estimate has 10 left: 10 or 0; the check's G2,
        which closed at 0 / 10). The fold's 30 is taken when said: 10 of the 30
        carried in is then from before the books. After a lock the same balance
        gives a range, 0 to 20, and 30 is refused."""
        ask = _asked(_nets(**self.STD), {2021: 30.0}, carried_in=30.0, typed_in=True)
        ct = ask["carry_back_cant_tell"]
        self.assertEqual((ct["years"], ct["books"], ct["least"]),
                         ([2022, 2023], {"2022": 30.0, "2023": 0.0},
                          {"2022": 0.0, "2023": 0.0}))
        self.assertEqual((ct["if_all"]["carried_back"], ct["if_none"]["carried_back"]),
                         (0.0, 30.0))
        g2 = _nets(y2021=100.0, y2022=-50.0, y2023=20.0, y2024=20.0)
        ct = _asked(g2, {2021: 30.0}, carried_in=30.0, typed_in=True)["carry_back_cant_tell"]
        self.assertEqual((ct["if_all"]["carried_back"], ct["if_none"]["carried_back"],
                          ct["if_none"]["loss_left"]), (0.0, 10.0, 10.0))
        said = _asked(_nets(**self.STD), {2021: 30.0}, carried_in=30.0, typed_in=True,
                      stated={2022: 30.0, 2023: 0.0})
        self.assertNotIn("carry_back_cant_tell", said)
        self.assertEqual(said["carried_in_before_books"][0]["before_books"], 10.0)
        lock = {2021: (0.0, 0.0, 0.0)}
        ct = _asked(_nets(**self.STD), {2021: 30.0}, locks=lock, carried_in=30.0,
                    typed_in=True)["carry_back_cant_tell"]
        self.assertEqual((ct["books"], ct["least"]), ({"2022": 20.0, "2023": 0.0},
                                                      {"2022": 0.0, "2023": 0.0}))
        code, _led, text = self._said(_nets(**self.STD), {2021: 30.0}, locks=lock,
                                      carried_in=30.0, typed_in=True,
                                      stated={2022: 30.0, 2023: 0.0})
        self.assertEqual(code, 1)
        self.assertIn("[carryover] carried_back (2022 = 30.00, 2023 = 0.00) leaves 20.00 "
                      "carried into 2024 (0.00 if the gains between took all they could), "
                      "but [estimate] other_losses says 30.00:", text)

    @rule(R.CA_CARRY_04)
    def test_a_first_lock_s_typed_opening(self):
        """Round 1's scenario A: only 2023 closed, on other_losses = 50, after a
        2022 loss of 50. With no lock before 2022 the 50 is a floor of 0: asked
        (as is a lock opened from nothing). After a lock, it says 2022 carried
        back nothing: the claim of 30 is 2024's, with no question."""
        lock = {2023: (0.0, 20.0, 30.0)}
        led = _asked(_nets(**self.STD), {2021: 30.0}, locks=lock, typed={2023: 50.0},
                     carried_in=30.0)
        self.assertEqual(led["carry_back_cant_tell"]["years"], [2022])
        none = _asked(_nets(**self.STD), {2021: 30.0}, locks={2023: (0.0, 0.0, 0.0)},
                      carried_in=0.0)
        self.assertEqual(none["carry_back_cant_tell"]["years"], [2022])
        led = _asked(_nets(**self.STD), {2021: 30.0}, typed={2023: 50.0}, carried_in=30.0,
                     locks={2021: (0.0, 0.0, 0.0), **lock})
        self.assertNotIn("carry_back_cant_tell", led)
        self.assertEqual((_split(led, 2022), _split(led)), ((0.0, 50.0), (30.0, 30.0)))
        self.assertEqual(_row(led, 2022)["carried_back_inferred"]["into"], 2023)

    def test_equal_amounts_are_never_printed_as_can_t_tell(self):
        """TJ-4884: where the balance is the same either way, it is printed as
        one figure, and the question is the year's carry-back."""
        from taxjson.bin.taxjson_carryover import render, render_brief
        led = _asked(_nets(**self.STD), {2021: 30.0})
        ct = led["carry_back_cant_tell"]
        self.assertEqual(ct["if_all"]["carryforward"], ct["if_none"]["carryforward"])
        for text in (render_brief(led, "CAD", None), render(led, "CAD", None, True)):
            text = " ".join(text.split())
            self.assertIn("Net-capital-loss carryforward after 2024: 50.00 T1A carry-back "
                          "of 2024's loss: can't tell (0.00 if 2022 and 2023 carried back "
                          "what these books show, 30.00 if nothing)", text)
            self.assertNotRegex(text, r"can't tell \((\d[\d,.]*) if [^()]*, \1 if nothing\)")

    @rule(R.CA_CARRY_04)
    def test_a_claim_out_of_the_year_s_reach_asks_nothing(self):
        """The review's P2: 2021's lock gone, claims under 2020 and 2023. Whatever
        2021's return carried back to 2020, 2024 reaches back to 2021 at most: its
        row is 0 / 20 either way, and it closes."""
        nets = _nets(y2020=100.0, y2021=-50.0, y2022=0.0, y2023=80.0, y2024=-20.0)
        locks = {2022: (0.0, 0.0, 50.0), 2023: (0.0, 50.0, 0.0)}
        opening = {2022: 50.0, 2023: 50.0}
        led = _asked(nets, {2020: 40.0, 2023: 20.0}, locks=locks, opening=opening)
        self.assertNotIn("carry_back_cant_tell", led)
        self.assertEqual(_split(led), (0.0, 20.0))
        self.assertEqual(_row(led, 2022)["claims_given_back"], 40.0)
        from taxjson.bin.taxjson_carryover import render_brief
        text = render_brief(led, "CAD", None)
        self.assertIn("carryforward after 2024: 20.00", text)
        self.assertNotIn("can't tell", text)

    @rule(R.CA_CARRY_04)
    def test_no_lock_at_all_with_a_rebuilt_year_between(self):
        """Round 2's P1: no lock, [estimate] other_losses typed in, nets +100 / -50
        / +20 / -30, a claim of 30 under 2021. With 2022 alone at nothing the rebuilt
        2023 row met the claim from the balance it took in, so both readings said 0
        for 2024 and it closed at 0 / 60 with no word. Every unchecked year is in the
        "nothing" reading, and each one that could have carried back is asked."""
        led = _asked(_nets(**self.STD), {2021: 30.0})
        ct = led["carry_back_cant_tell"]
        self.assertEqual((ct["years"], ct["books"]), ([2022, 2023], {"2022": 30.0, "2023": 0.0}))
        self.assertEqual((ct["if_none"]["carried_back"], ct["if_all"]["carried_back"]),
                         (30.0, 0.0))
        from taxjson.lib.carry_back import cant_tell_text
        self.assertIn("add `carried_back = { 2022 = 30.00, 2023 = 0 }` if they did",
                      cant_tell_text(led))
        one = _asked(_nets(**self.STD), {2021: 30.0}, stated={2022: 0.0})
        self.assertEqual(one["carry_back_cant_tell"]["years"], [2023])
        self.assertEqual(one["carry_back_cant_tell"]["answered"], {"2022": 0.0})
        self.assertIn("`carried_back = { 2022 = 0.00, 2023 = 0 }` if not (then 30.00)",
                      cant_tell_text(one))
        both = _asked(_nets(**self.STD), {2021: 30.0}, stated={2022: 0.0, 2023: 0.0})
        self.assertNotIn("carry_back_cant_tell", both)
        self.assertEqual(_row(both, 2024)["carried_back"], 30.0)

    @rule(R.CA_CARRY_04)
    def test_last_year_s_loss_and_a_loss_this_year(self):
        """The ruling of 2026-11-21: a first year with taxjson, last year's loss
        carried back on its own return, a loss this year too. The books can't show
        which return carried it: asked once, in a line that can be pasted."""
        from taxjson.lib.carry_back import cant_tell_text
        nets = _nets(y2021=100.0, y2022=20.0, y2023=-50.0, y2024=-30.0)
        led = _asked(nets, {2021: 40.0})
        self.assertEqual(
            cant_tell_text(led),
            "these books can't tell whether the return of 2023 carried a loss back "
            "(T1A) to the [carryover] claimed amounts, or this year's does. Under "
            "[carryover] in taxjson.toml add `carried_back = { 2023 = 40.00 }` if it "
            "did, as the books read (this year then carries back 0.00), or "
            "`carried_back = { 2023 = 0 }` if not (then 40.00); or put its close-year "
            "lock in filed/")
        told = _asked(nets, {2021: 40.0}, stated={2023: 40.0})
        self.assertNotIn("carry_back_cant_tell", told)
        self.assertEqual((_split(told, 2023), _split(told)), ((40.0, 10.0), (0.0, 40.0)))

    def _said(self, nets, claimed, **case):
        import contextlib
        import io
        err = io.StringIO()
        code, led = None, None
        with contextlib.redirect_stderr(err):
            try:
                led = _asked(nets, claimed, **case)
            except SystemExit as e:
                code = e.code
        return code, led, " ".join(err.getvalue().split())

    @rule(R.CA_CARRY_04)
    def test_an_answer_the_ledger_no_longer_needs_is_a_note(self):
        """Round 2's P2: an entry for a year a lock or the books now answer the same
        way, or that nothing of the year rests on, is said to be removable and
        changes nothing; a taxjson.toml carried into the next year closes."""
        nets = _nets(y2021=100.0, y2022=-50.0, y2023=0.0, y2024=-30.0)
        cases = (
            # the lock's own year, as its lock records
            (dict(lock2023=(0.0, 0.0, 50.0), opening=50.0, stated={2023: 0.0}),
             "2023 is no longer needed: 2023's close-year lock records the same"),
            # the rebuilt row a lock checks, as the books show
            (dict(lock2023=(0.0, 0.0, 10.0), opening=10.0, stated={2022: 40.0}),
             "2022 is no longer needed: the books and the lock after it show the same"),
            # a year that could carry nothing back, whatever it says
            (dict(lock2023=(0.0, 0.0, 50.0), opening=50.0, stated={2021: 7.0}),
             "2021 is no longer needed: nothing of 2024 rests on it"),
        )
        for case, note in cases:
            with self.subTest(case=case):
                code, led, said = self._said(nets, {2021: 40.0}, **case)
                self.assertIsNone(code, said)
                self.assertIn(note + "; it can be removed from taxjson.toml", said)
                plain = _asked(nets, {2021: 40.0}, **dict(case, stated=None))
                self.assertEqual(led, plain)
        # scenario D: the answer given when 2021 was asked about, now out of reach
        nets = _nets(y2020=100.0, y2021=-50.0, y2022=0.0, y2023=80.0, y2024=-20.0)
        code, led, said = self._said(
            nets, {2020: 40.0, 2023: 20.0}, stated={2021: 0.0},
            locks={2022: (0.0, 0.0, 50.0), 2023: (0.0, 50.0, 0.0)},
            opening={2022: 50.0, 2023: 50.0})
        self.assertIsNone(code)
        self.assertIn("2021 is no longer needed: nothing of 2024 rests on it", said)
        self.assertEqual(_split(led), (0.0, 20.0))

    @rule(R.CA_CARRY_04)
    def test_an_answer_that_contradicts_or_can_t_be_is_refused(self):
        nets = _nets(y2021=100.0, y2022=-50.0, y2023=0.0, y2024=-30.0)
        cases = (
            (dict(lock2023=(0.0, 0.0, 10.0), opening=10.0, stated={2022: 0.0}),
             "says 2022 carried back 0.00, but the books and the lock after it show 40.00"),
            (dict(lock2023=(0.0, 0.0, 50.0), opening=50.0, stated={2023: 5.0}),
             "says 2023 carried back 5.00, but 2023's close-year lock records 0.00"),
            (dict(lock2023=(0.0, 0.0, 50.0), opening=50.0, stated={2024: 3.0}),
             "names 2024, not a year before the project year"),
            # Round 2's P3: more than the claims that year could meet
            (dict(lock2023=(0.0, 0.0, 50.0), opening=50.0, stated={2022: 500.0}),
             "says 2022 carried back 500.00, more than the 40.00 of [carryover] claimed "
             "amounts a 2022 loss could meet"),
        )
        for case, why in cases:
            with self.subTest(case=case):
                code, _led, said = self._said(nets, {2021: 40.0}, **case)
                self.assertEqual(code, 1, said)
                self.assertIn("[carryover] carried_back " + why, said)
                self.assertIn("entry in taxjson.toml" if "more than" in why
                              else "remove that entry from taxjson.toml", said)

    def test_the_ledger_s_entry_is_read_whole_or_not_at_all(self):
        from taxjson.lib.carry_back import cant_tell, cant_tell_text
        fig = {"carried_back": 1.0, "carryforward": 2.0}
        books = {"2021": 5.0, "2022": 0.0}
        good = {"carry_back_cant_tell": {"years": [2021, 2022], "books": books,
                                         "if_none": fig, "if_all": fig}}
        self.assertEqual(cant_tell(good)["years"], [2021, 2022])
        self.assertIn("{ 2021 = 5.00, 2022 = 0 }", cant_tell_text(good))
        good["carry_back_cant_tell"]["if_none"] = dict(fig, carried_back=3.0)
        self.assertIn("carries back 1.00), or `carried_back = { 2021 = 0, 2022 = 0 }` if "
                      "not (then 3.00)", cant_tell_text(good))
        good["carry_back_cant_tell"]["if_none"] = dict(fig, carryforward=9.0)
        self.assertIn("carries forward 2.00)", cant_tell_text(good))
        self.assertIn("if not (then 9.00)", cant_tell_text(good))
        self.assertIn("the returns of 2021 and 2022", cant_tell_text(good))
        for bad in ({}, {"rows": "x"}, {"carry_back_cant_tell": {"years": []}},
                    {"carry_back_cant_tell": {"years": [2021], "books": books,
                                              "if_none": fig}},
                    {"carry_back_cant_tell": {"years": [2021], "books": {},
                                              "if_none": fig, "if_all": fig}},
                    {"carry_back_cant_tell": {"years": [2021], "books": books,
                                              "answered": {"2020": "x"},
                                              "if_none": fig, "if_all": fig}},
                    {"carry_back_cant_tell": {"years": ["x"], "books": books,
                                              "if_none": fig, "if_all": fig}}):
            self.assertIsNone(cant_tell(bad), bad)
            self.assertEqual(cant_tell_text(bad), "")


class TestAChainStartedAfterALossYear(unittest.TestCase):
    """TJ-4884, end to end, as a v0.41.0 user meets it: taxjson started in 2024,
    after 2023's loss of 100 and its T1A back to 2022 (`claimed = { 2022 = 40 }`),
    the first lock 2024's (opened from [estimate] other_losses = 60, or from
    nothing). `tjs carryover`, the estimate and `tjs close-year` read one answer."""
    def _years(self, top, last_sale, other_losses):
        (top / "inputs" / "margin").mkdir(parents=True)
        sales = {2022: ("ZZQA", 600.0), 2023: ("ZZQB", 400.0), 2024: ("ZZQC", 550.0),
                 2025: ("ZZQD", last_sale)}
        (top / "inputs" / "margin" / "t.tt").write_text(
            "".join(_trades(yr, *sale) for yr, sale in sorted(sales.items())))
        dirs = []
        for year in (2024, 2025):
            d = top / str(year)
            d.mkdir()
            toml = _TOML.replace("year = 2023", f"year = {year}")
            if year == 2025:
                toml = toml.replace(
                    "[accounts.margin]",
                    'prior_year_record = "../2024/filed/2024.json"\n\n[accounts.margin]')
            elif other_losses:
                toml += f"other_losses = {other_losses}\n"
            (d / "taxjson.toml").write_text(
                toml + "\n[carryover]\nclaimed = { 2022 = 40.00 }\n")
            dirs.append(d)
        return dirs

    def _says(self, d):
        """(carryover says can't tell, the estimate says unknown), which agree."""
        res = _ok(_cli(d, "carryover", "--json"))
        asked = "carry_back_cant_tell" in json.loads(res.stdout)
        for flags in ((), ("--details",)):
            self.assertEqual("can't tell" in _ok(_cli(d, "carryover", *flags)).stdout,
                             asked, flags)
        est = json.loads(_ok(_cli(d, "estimate", "--json")).stdout)["estimate"]
        self.assertEqual("carry_back_unknown" in est, asked)
        return asked

    def _ncl(self, d):
        return json.loads((d / "filed" / f"{d.name}.json").read_text()
                          )["carryforwards"]["net_capital_loss"]

    @rule(R.CA_CARRY_04)
    def test_gain_years_close_with_no_question(self):
        for other_losses, into_2025 in ((60, 10.0), (None, 0.0)):
            with self.subTest(other_losses=other_losses), \
                    tempfile.TemporaryDirectory() as td:
                d24, d25 = self._years(Path(td), 530.0, other_losses)
                _ok(_cli(d24, "run", "--no-input"))
                self.assertFalse(self._says(d24))
                _ok(_cli(d24, "close-year", "--yes"))
                ncl = self._ncl(d24)
                self.assertEqual((ncl.get("carried_back", 0.0), ncl["closing"]),
                                 (0.0, into_2025))
                _ok(_cli(d25, "run", "--no-input"))
                self.assertFalse(self._says(d25))
                _ok(_cli(d25, "handoff"))
                _ok(_cli(d25, "close-year", "--yes"))
                ncl = self._ncl(d25)
                self.assertEqual((ncl["opening"], ncl.get("carried_back", 0.0),
                                  ncl["closing"]), (into_2025, 0.0, 0.0))

    @rule(R.CA_CARRY_04)
    def test_a_loss_year_after_it(self):
        """With the 60 typed in, 2023's carry-back is settled and 2025's loss is
        simply carried forward. With nothing typed, all three ask, once."""
        with tempfile.TemporaryDirectory() as td:
            d24, d25 = self._years(Path(td), 470.0, 60)
            _ok(_cli(d24, "run", "--no-input"))
            _ok(_cli(d24, "close-year", "--yes"))
            _ok(_cli(d25, "run", "--no-input"))
            self.assertFalse(self._says(d25))
            _ok(_cli(d25, "close-year", "--yes"))
            ncl = self._ncl(d25)
            self.assertEqual((ncl["opening"], ncl.get("carried_back", 0.0), ncl["closing"]),
                             (10.0, 0.0, 40.0))
        with tempfile.TemporaryDirectory() as td:
            d24, d25 = self._years(Path(td), 470.0, None)
            _ok(_cli(d24, "run", "--no-input"))
            _ok(_cli(d24, "close-year", "--yes"))
            _ok(_cli(d25, "run", "--no-input"))
            self.assertTrue(self._says(d25))
            closed = _cli(d25, "close-year", "--yes")
            self.assertEqual(closed.returncode, 1, closed.stdout + closed.stderr)
            self.assertIn("add `carried_back = { 2023 = 40.00 }` if it did",
                          " ".join((closed.stdout + closed.stderr).split()))
            toml = d25 / "taxjson.toml"
            toml.write_text(toml.read_text().replace(
                "claimed =", "carried_back = { 2023 = 40.00 }\nclaimed ="))
            self.assertFalse(self._says(d25))
            _ok(_cli(d25, "close-year", "--yes"))
            ncl = self._ncl(d25)
            self.assertEqual((ncl["opening"], ncl.get("carried_back", 0.0), ncl["closing"]),
                             (0.0, 0.0, 30.0))
            self.assertEqual(ncl["carry_back_stated"], {"2023": 40.0})


class TestAGainYearWithALossLeft(unittest.TestCase):
    """TJ-4885, end to end (the check's scenarios): 2022 +100 (closed), 2023 -100
    with no lock, `claimed = { 2022 = 40 }`, 2024 +80; in year folders and in one
    folder; and no lock at all with a gain of 20 under a balance of 30. The three
    commands agree, and the lock is a reading the balance typed in allows."""
    SALES = {2022: ("ZZQA", 600.0), 2023: ("ZZQB", 400.0), 2024: ("ZZQC", 580.0)}

    def _project(self, top, single, other_losses, sales=None, claim="2022 = 40.00",
                 first=2022, extra="", rows=""):
        sales = sales or self.SALES
        tail = ((f"other_losses = {other_losses}\n" if other_losses else "")
                + f"\n[carryover]\nclaimed = {{ {claim} }}\n" + extra)
        if single:
            d = top / "proj"
            (d / "inputs" / "margin").mkdir(parents=True)
            (d / "inputs" / "margin" / "t.tt").write_text(
                "".join(_trades(yr, *sale) for yr, sale in sorted(sales.items())) + rows)
            base = _TOML.replace('inputs_dir = "../inputs"\n', "")
            (d / "taxjson.toml").write_text(base.replace("year = 2023", f"year = {first}"))
            _ok(_cli(d, "run", "--no-input"))
            _ok(_cli(d, "close-year", "--yes"))
            (d / "taxjson.toml").write_text(base.replace("year = 2023", "year = 2024") + tail)
        else:
            (top / "inputs" / "margin").mkdir(parents=True)
            (top / "inputs" / "margin" / "t.tt").write_text(
                "".join(_trades(yr, *sale) for yr, sale in sorted(sales.items())) + rows)
            for year in ((first, 2024) if first else (2024,)):
                d = top / str(year)
                d.mkdir()
                (d / "taxjson.toml").write_text(
                    _TOML.replace("year = 2023", f"year = {year}")
                    + (tail if year == 2024 else ""))
                _ok(_cli(d, "run", "--no-input"))
                if year != 2024:
                    _ok(_cli(d, "close-year", "--yes"))
        _ok(_cli(d, "run", "--no-input"))
        return d

    def _agree(self, d):
        """Whether the three ask; the estimate's figures; the ledger's rows."""
        res = _ok(_cli(d, "carryover", "--json"))
        led = json.loads(res.stdout)
        asked = "carry_back_cant_tell" in led
        self.assertEqual("can't tell" in _ok(_cli(d, "carryover")).stdout, asked)
        est = json.loads(_ok(_cli(d, "estimate", "--json")).stdout)["estimate"]
        self.assertEqual("carry_back_unknown" in est, asked)
        # One project-year figure: the ledger's net is the estimate's.
        net = _row(led, 2024)["net_gain"]
        self.assertEqual((max(0.0, -net), min(max(0.0, net), est["losses_opening"])),
                         (est["losses_created"], est["losses_applied"]))
        return asked, est, led

    def _ncl(self, d):
        return json.loads((d / "filed" / "2024.json").read_text()
                          )["carryforwards"]["net_capital_loss"]

    @rule(R.CA_CARRY_04)
    def test_the_balance_typed_in_decides_where_it_can(self):
        """One folder: 2022's lock is read, so the balance typed in says what 2023
        carried back. Year folders with no prior record named: no lock is read, the
        balance is a floor, and 100 (at least 0, at most 40) is asked."""
        # other_losses: (2023's carry-back, the lock's applied / carried back / closing)
        want = {None: (40.0, (0.0, 0.0, 0.0)), 100: (0.0, (80.0, 20.0, 0.0)),
                60: (40.0, (60.0, 0.0, 0.0))}
        for single in (False, True):
            for other_losses, (back23, lock) in want.items():
                with self.subTest(single=single, other_losses=other_losses), \
                        tempfile.TemporaryDirectory() as td:
                    d = self._project(Path(td), single, other_losses)
                    asked, est, led = self._agree(d)
                    self.assertEqual(asked, other_losses == 100 and not single)
                    if asked:
                        closed = _cli(d, "close-year", "--yes")
                        self.assertEqual(closed.returncode, 1, closed.stdout + closed.stderr)
                        self.assertIn("add `carried_back = { 2023 = 40.00 }` if it did",
                                      " ".join((closed.stdout + closed.stderr).split()))
                        toml = d / "taxjson.toml"
                        toml.write_text(toml.read_text().replace(
                            "claimed =", "carried_back = { 2023 = 0 }\nclaimed ="))
                        asked, est, led = self._agree(d)
                        self.assertFalse(asked)
                    self.assertEqual(_row(led, 2023)["carried_back"], back23)
                    self.assertEqual("carried_back_inferred" in _row(led, 2023),
                                     other_losses is not None
                                     and (single or other_losses == 60))
                    self.assertEqual((est["losses_carried_back"],
                                      est["losses_carried_forward"]), lock[1:])
                    _ok(_cli(d, "close-year", "--yes"))
                    ncl = self._ncl(d)
                    self.assertEqual((ncl["applied"], ncl.get("carried_back", 0.0),
                                      ncl["closing"]), lock)

    @rule(R.CA_CARRY_04)
    def test_an_entry_against_the_balance(self):
        """After a lock the balance answers: the same entry is a note, another is
        refused. With no lock before, an entry that leaves less than the balance
        is taken: the rest was carried in from before the books, and is said."""
        with tempfile.TemporaryDirectory() as td:
            d = self._project(Path(td), True, 100)
            toml = d / "taxjson.toml"
            kept = toml.read_text()
            toml.write_text(kept.replace("claimed =", "carried_back = { 2023 = 0 }\nclaimed ="))
            res = _ok(_cli(d, "carryover", "--json"))
            self.assertIn("2023 is no longer needed: the 100.00 carried into 2024",
                          " ".join(res.stderr.split()))
            self.assertNotIn("carried_back_stated", _row(json.loads(res.stdout), 2023))
            toml.write_text(kept.replace("claimed =", "carried_back = { 2023 = 40 }\nclaimed ="))
            for cmd in (("carryover",), ("close-year", "--yes")):
                res = _cli(d, *cmd)
                self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
                self.assertIn("says 2023 carried back 40.00, but the 100.00 carried into "
                              "2024 ([estimate] other_losses) says 0.00",
                              " ".join(res.stderr.split()))
        with tempfile.TemporaryDirectory() as td:
            d = self._project(Path(td), False, 100)
            toml = d / "taxjson.toml"
            toml.write_text(toml.read_text().replace(
                "claimed =", "carried_back = { 2023 = 40 }\nclaimed ="))
            asked, est, led = self._agree(d)
            self.assertFalse(asked)
            self.assertEqual(led["carried_in_before_books"][0]["before_books"], 40.0)
            self.assertIn("the 100.00 you carried in holds 40.00 from before these books",
                          " ".join(_ok(_cli(d, "carryover")).stdout.split()))
            _ok(_cli(d, "close-year", "--yes"))
            ncl = self._ncl(d)
            self.assertEqual((ncl["applied"], ncl.get("carried_back", 0.0), ncl["closing"]),
                             (80.0, 0.0, 20.0))

    @rule(R.CA_CARRY_03)
    def test_an_estimate_on_another_balance_reads_no_split(self):
        """`tjs estimate --other-losses N` is not the project's balance, which the
        ledger folds on: the split is unknown there, never one made for another."""
        with tempfile.TemporaryDirectory() as td:
            d = self._project(Path(td), True, 100)
            res = _ok(_cli(d, "estimate", "--json", "--other-losses", "90"))
            est = json.loads(res.stdout)["estimate"]
            self.assertIn("on the balance this project carries in (100.00), not on the "
                          "90.00 given here (--other-losses)", est["carry_back_unknown"])
            self.assertNotIn("losses_carried_forward", est)
            same = json.loads(_ok(_cli(d, "estimate", "--json")).stdout)["estimate"]
            self.assertEqual(same["losses_carried_back"], 20.0)

    @rule(R.CA_CARRY_04)
    def test_no_lock_and_a_gain_smaller_than_the_balance(self):
        """The check's G2: +100, -50, +20, +20, other_losses = 30, a claim of 30
        under 2021. It closed at 0 / 10 with no word. 2022 carried back anything
        from nothing to the claim, and the 10 the estimate has left is carried back
        or not: asked by all three; said to be nothing, 10 / 0."""
        sales = {2021: ("ZZQA", 600.0), 2022: ("ZZQB", 450.0), 2023: ("ZZQC", 520.0),
                 2024: ("ZZQD", 520.0)}
        with tempfile.TemporaryDirectory() as td:
            d = self._project(Path(td), False, 30, sales, "2021 = 30.00", first=None)
            asked, est, _led = self._agree(d)
            self.assertTrue(asked)
            toml = d / "taxjson.toml"
            toml.write_text(toml.read_text().replace(
                "claimed =", "carried_back = { 2022 = 0, 2023 = 0 }\nclaimed ="))
            asked, est, _led = self._agree(d)
            self.assertFalse(asked)
            self.assertEqual((est["losses_carried_back"], est["losses_carried_forward"]),
                             (10.0, 0.0))
            _ok(_cli(d, "close-year", "--yes"))
            ncl = self._ncl(d)
            self.assertEqual((ncl["applied"], ncl["carried_back"], ncl["closing"]),
                             (20.0, 10.0, 0.0))

    @rule(R.CA_CARRY_04)
    def test_a_t5_gain_that_turns_a_books_loss_into_a_gain(self):
        """The sign of the year: the ledger's net and the estimate's are one figure,
        the RETURN row and the T5 box 18 gains (neither nets T3 gains or the FX on
        cash). The books lose 30 in 2024 and box 18 adds 50: a gain of 20 for both,
        nothing left to split, nothing asked. Without the slip, a loss: asked."""
        sales = {**self.SALES, 2024: ("ZZQC", 470.0)}
        slip = ('\n[[capital_gains_dividends]]\nsymbol = "ZZQC.TO"\nyear = 2024\n'
                'amount = 50.00\n')
        with tempfile.TemporaryDirectory() as td:
            d = self._project(Path(td), False, None, sales, extra=slip, rows=(
                "DIVIDEND 2024-06-14 16:00:00 ZZQC.TO 50 CAD 1.00 50.00\n"))
            asked, est, led = self._agree(d)
            self.assertFalse(asked)
            self.assertEqual((_row(led, 2024)["net_gain"], est["losses_created"]), (20.0, 0.0))
            _ok(_cli(d, "close-year", "--yes"))
        with tempfile.TemporaryDirectory() as td:
            d = self._project(Path(td), False, None, sales)
            asked, est, led = self._agree(d)
            self.assertTrue(asked)
            self.assertEqual((_row(led, 2024)["net_gain"], est["losses_created"]), (-30.0, 30.0))
            closed = _cli(d, "close-year", "--yes")
            self.assertEqual(closed.returncode, 1, closed.stdout + closed.stderr)


class TestTheAnswerKey(unittest.TestCase):
    """[carryover] carried_back: read as `claimed` is, Canada's, laid out by `tjs
    format` when set and never offered by `tjs init`."""
    def test_it_is_read_and_checked(self):
        from taxjson.lib.project_tables import carried_back_stated, claimed_losses
        cfg = {"carryover": {"claimed": {"2021": 40.0}, "carried_back": {"2022": 0}}}
        self.assertEqual(carried_back_stated(cfg), ({2022: 0.0}, []))
        self.assertEqual(claimed_losses(cfg), ({2021: 40.0}, []))
        for bad in ({"2022": -1}, {"2022": "40"}, {"x": 1}, 40):
            got, problems = carried_back_stated({"carryover": {"carried_back": bad}})
            self.assertEqual(got, {})
            self.assertIn("[carryover] carried_back", problems[0])
        self.assertIn("unknown key 'carry_back'",
                      claimed_losses({"carryover": {"carry_back": {}}})[1][0])

    def test_it_is_canada_s(self):
        from taxjson.lib.config_check import settings_problems
        cfg = {"settings": {"year": 2024, "country": "usa", "base_currency": "USD"},
               "accounts": {"margin": {"type": "taxable"}},
               "carryover": {"carried_back": {"2022": 0}}}
        self.assertTrue(any("[carryover] carried_back" in p for p in settings_problems(cfg)))
        cfg["settings"].update(country="canada", base_currency="CAD", province="ON")
        self.assertFalse(any("carried_back" in p for p in settings_problems(cfg)))

    def test_the_template_doesn_t_offer_it_and_format_keeps_it(self):
        from taxjson.lib import config_template as CT
        self.assertNotIn("carried_back", CT.render_init("canada", 2024)[0])
        self.assertNotIn("carried_back", CT.spec_keys()["carryover"])
        self.assertIn("carried_back", CT.validator_keys()["carryover"])
        text = CT.render_init("canada", 2024)[0] + (
            "\n[carryover]\nclaimed = { 2021 = 40.00 }\ncarried_back = { 2022 = 0 }\n")
        res = CT.format_config(text)
        self.assertEqual(res.unrecognised, [])
        self.assertRegex(res.text, r"(?m)^carried_back\s+= \{ 2022 = 0 \}$")
        self.assertEqual(CT.format_config(res.text).text, res.text)


class TestTheRecordBehindTheLock(unittest.TestCase):
    """TJ-1574: in year folders 2024 names 2023's lock, which opened from 2022's.
    That record says what 2022's return carried back, so the ledger reads it; when
    it is gone the ledger can't tell a claim never met from one met by a loss these
    books no longer hold, and no figure is carried or locked on a guess."""
    def _project(self, top, sales=None, claim="2021 = 40.00"):
        _build_years(top, {2024: claim},
                     {**(sales or _SALES), 2024: ("ZZQD", 470.0)},
                     close=(2021, 2022, 2023))
        return top / "2024"

    def _lock(self, y):
        return json.loads((y / "filed" / "2024.json").read_text()
                          )["carryforwards"]["net_capital_loss"]

    @rule(R.CA_CARRY_04)
    def test_the_ledger_reads_it_and_has_nothing_to_guess(self):
        """2022's return carried back nothing (its lock): the 40 waits for 2024.
        And an answer nobody asked for is refused."""
        with tempfile.TemporaryDirectory() as td:
            y = self._project(Path(td))
            res = _ok(_cli(y, "carryover", "--json"))
            led = json.loads(res.stdout)
            self.assertNotIn("carry_back_cant_tell", led)
            for year in (2021, 2022, 2023):
                self.assertTrue(_row(led, year)["balance_from_lock"], year)
                self.assertNotIn("claims_given_back", _row(led, year))
            self.assertEqual(_row(led, 2022)["carried_back"], 0.0)
            self.assertEqual(_split(led), (40.0, 20.0))
            self.assertNotIn("can't tell", _ok(_cli(y, "carryover", "--details")).stdout)
            est = json.loads(_ok(_cli(y, "estimate", "--json")).stdout)["estimate"]
            self.assertEqual((est["losses_carried_back"], est["losses_carried_forward"]),
                             (40.0, 20.0))
            self.assertNotIn("carry_back_unknown", est)
            toml = y / "taxjson.toml"
            kept = toml.read_text()
            toml.write_text(kept.replace("claimed =", "carried_back = { 2022 = 40 }\nclaimed ="))
            for cmd in (("carryover",), ("close-year", "--yes")):
                res = _cli(y, *cmd)
                self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
                self.assertIn("[carryover] carried_back says 2022 carried back 40.00, but "
                              "2022's close-year lock records 0.00",
                              " ".join(res.stderr.split()))
                self.assertIn("remove that entry from taxjson.toml",
                              " ".join(res.stderr.split()))
            self.assertFalse((y / "filed" / "2024.json").exists())
            toml.write_text(kept)
            _ok(_cli(y, "close-year", "--yes"))
            self.assertNotIn("carry_back_stated", self._lock(y))

    @rule(R.CA_CARRY_04)
    def test_a_real_carry_back_isn_t_undone(self):
        """The backlog's shape: 2022's return did carry 40 back, from a loss of 90
        of which these books hold 50. Its lock (back 40, closing 50) is the record:
        2024 carries back nothing and 80 forward. Before, the 40 went back to
        waiting and 2024 met it again: back 40, 40 forward."""
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            sales = {2021: ("ZZQA", 600.0), 2022: ("ZZQB", 450.0), 2023: ("ZZQC", 500.0)}
            y = self._project(top, sales)
            for year, back, closing in ((2022, 40.0, 50.0), (2023, 0.0, 50.0)):
                lock = top / str(year) / "filed" / f"{year}.json"
                doc = json.loads(lock.read_text())
                ncl = doc["carryforwards"]["net_capital_loss"]
                if year == 2022:
                    doc["return_row"]["realized"] = -90.0
                    ncl.update(created=90.0, carried_back=back, closing=closing)
                else:
                    self.assertEqual((ncl["opening"], ncl["closing"]), (50.0, 50.0))
                lock.write_text(json.dumps(doc))
            led = json.loads(_ok(_cli(y, "carryover", "--json")).stdout)
            self.assertNotIn("carry_back_cant_tell", led)
            self.assertEqual(_split(led, 2022), (40.0, 50.0))
            self.assertEqual(_split(led), (0.0, 80.0))

    @rule(R.CA_CARRY_04)
    def test_without_it_the_views_say_so_and_the_estimate_and_close_refuse(self):
        """...and each answer settles it: the key, or that year's lock in filed/."""
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            y = self._project(top)
            gone = top / "2022" / "filed" / "2022.json"
            kept = gone.read_text()
            gone.unlink()
            res = _ok(_cli(y, "carryover", "--json"))
            led = json.loads(res.stdout)
            ct = led["carry_back_cant_tell"]
            self.assertEqual(ct["years"], [2022])
            self.assertEqual((ct["if_none"]["carried_back"], ct["if_none"]["carryforward"],
                              ct["if_all"]["carried_back"], ct["if_all"]["carryforward"]),
                             (40.0, 20.0, 0.0, 60.0))
            self.assertEqual(_row(led, 2023)["claims_given_back"], 40.0)
            for flags in ((), ("--details",)):
                report = " ".join(_ok(_cli(y, "carryover", *flags)).stdout.split())
                self.assertIn("carryforward after 2024: can't tell (60.00 if 2022", report)
            self.assertIn("add `carried_back = { 2022 = 40.00 }` if it did", report)
            res = _ok(_cli(y, "estimate", "--json"))
            est = json.loads(res.stdout)["estimate"]
            self.assertIn("can't tell whether the return of 2022 carried a loss back",
                          est["carry_back_unknown"])
            self.assertIn("`carried_back = { 2022 = 0 }` if not (then 40.00)",
                          est["carry_back_unknown"])
            self.assertNotIn("losses_carried_back", est)
            self.assertNotIn("losses_carried_forward", est)
            self.assertIn("the carry-forward is unknown",
                          " ".join(_ok(_cli(y, "estimate")).stdout.split()))
            closed = _cli(y, "close-year", "--yes")
            self.assertEqual(closed.returncode, 1, closed.stdout + closed.stderr)
            said = " ".join((closed.stdout + closed.stderr).split())
            self.assertIn("add `carried_back = { 2022 = 40.00 }` if it did", said)
            self.assertIn("put its close-year lock in filed/", said)
            self.assertIn("Nothing was written", said)
            self.assertFalse((y / "filed" / "2024.json").exists())
            # One answer: that year's lock, in this project's filed/.
            (y / "filed").mkdir(exist_ok=True)
            (y / "filed" / "2022.json").write_text(kept)
            led = json.loads(_ok(_cli(y, "carryover", "--json")).stdout)
            self.assertNotIn("carry_back_cant_tell", led)
            self.assertEqual(_split(led), (40.0, 20.0))
            est = json.loads(_ok(_cli(y, "estimate", "--json")).stdout)["estimate"]
            self.assertEqual(est["losses_carried_back"], 40.0)
            (y / "filed" / "2022.json").unlink()
            # The other, in taxjson.toml: 2022's return carried back the 40
            # (from a loss these books hold, or not).
            toml = y / "taxjson.toml"
            toml.write_text(toml.read_text().replace(
                "claimed =", "carried_back = { 2022 = 40.00 }\nclaimed ="))
            led = json.loads(_ok(_cli(y, "carryover", "--json")).stdout)
            self.assertNotIn("carry_back_cant_tell", led)
            self.assertEqual(_row(led, 2022)["carried_back_stated"], 40.0)
            self.assertEqual(_split(led), (0.0, 60.0))
            self.assertIn("2022: carried back 40.00, your figure",
                          " ".join(_ok(_cli(y, "carryover")).stdout.split()))
            est = json.loads(_ok(_cli(y, "estimate", "--json")).stdout)["estimate"]
            self.assertEqual(est["carry_back_stated"], {"2022": 40.0})
            closed = _ok(_cli(y, "close-year", "--yes"))
            ncl = self._lock(y)
            self.assertEqual((ncl.get("carried_back", 0.0), ncl["closing"]), (0.0, 60.0))
            self.assertEqual(ncl["carry_back_stated"], {"2022": 40.0})
            self.assertIn("2022's carry-back 40.00 is your figure",
                          " ".join((closed.stdout + closed.stderr).split()))
            _ok(_cli(y, "check-filed"))
            _ok(_cli(y, "handoff"))
            # Round 2's P2: the next year, its taxjson.toml carried along. 2024's
            # lock now holds the answer: the entry is a note, and 2025 closes.
            d = top / "2025"
            d.mkdir()
            (d / "taxjson.toml").write_text(
                _TOML.replace("year = 2023", "year = 2025").replace(
                    "[accounts.margin]",
                    'prior_year_record = "../2024/filed/2024.json"\n\n[accounts.margin]')
                + "\n[carryover]\ncarried_back = { 2022 = 40.00 }\n"
                  "claimed = { 2021 = 40.00 }\n")
            _ok(_cli(d, "run", "--no-input"))
            res = _ok(_cli(d, "carryover", "--json"))
            self.assertNotIn("carry_back_cant_tell", json.loads(res.stdout))
            self.assertIn("carried_back's entry for 2022 is no longer needed",
                          " ".join(res.stderr.split()))
            _ok(_cli(d, "estimate", "--json"))
            _ok(_cli(d, "close-year", "--yes", "--force"))     # (no sale in 2025)
            self.assertTrue((d / "filed" / "2025.json").exists())

    @rule(R.CA_CARRY_04)
    def test_a_first_lock_typed_in_end_to_end(self):
        """The review's scenario A: only 2023 closed, on [estimate] other_losses =
        50; a claim of 30 under 2021 added in 2024. It closed at back 0, 60 forward
        with no word. The 50 typed in may hold a loss from before the books, so it
        settles nothing here (TJ-4885): asked; said that 2022's return carried back
        nothing, 30 / 30."""
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            _build_years(top, {}, {2023: _SALES[2023], 2024: ("ZZQD", 470.0)})
            (top / "inputs" / "margin" / "t.tt").write_text("".join(
                _trades(yr, *sale) for yr, sale in
                sorted({**_SALES, 2024: ("ZZQD", 470.0)}.items())))
            d23, y = top / "2023", top / "2024"
            d23toml = d23 / "taxjson.toml"
            d23toml.write_text(d23toml.read_text().replace(
                "[estimate]\n", "[estimate]\nother_losses = 50\n"))
            _ok(_cli(d23, "run", "--no-input"))
            _ok(_cli(d23, "close-year", "--yes"))
            toml = y / "taxjson.toml"
            toml.write_text(toml.read_text() + "\n[carryover]\nclaimed = { 2021 = 30.00 }\n")
            _ok(_cli(y, "run", "--no-input"))
            led = json.loads(_ok(_cli(y, "carryover", "--json")).stdout)
            self.assertEqual(led["carry_back_cant_tell"]["years"], [2022])
            closed = _cli(y, "close-year", "--yes")
            self.assertEqual(closed.returncode, 1, closed.stdout + closed.stderr)
            toml.write_text(toml.read_text().replace(
                "claimed =", "carried_back = { 2022 = 0 }\nclaimed ="))
            _ok(_cli(y, "close-year", "--yes"))
            ncl = self._lock(y)
            self.assertEqual((ncl["carried_back"], ncl["closing"]), (30.0, 30.0))
            self.assertEqual(ncl["carry_back_stated"], {"2022": 0.0})

    @rule(R.CA_CARRY_04)
    def test_a_damaged_record_behind_is_left_out_not_a_stop(self):
        """The review's scenario B: 2021's lock, a file 2024 never names, has no
        `totals` (or is no JSON). The ledger doesn't read it, and 2024 closes as
        it did: 40 / 20."""
        for damage in ("totals", "json"):
            with self.subTest(damage=damage), tempfile.TemporaryDirectory() as td:
                top = Path(td)
                y = self._project(top)
                rec = top / "2021" / "filed" / "2021.json"
                doc = json.loads(rec.read_text())
                doc.pop("totals")
                rec.write_text(json.dumps(doc) if damage == "totals" else "{not json")
                led = json.loads(_ok(_cli(y, "carryover", "--json")).stdout)
                self.assertNotIn("balance_from_lock", _row(led, 2021))
                self.assertTrue(_row(led, 2022)["balance_from_lock"])
                self.assertNotIn("carry_back_cant_tell", led)
                _ok(_cli(y, "close-year", "--yes"))
                ncl = self._lock(y)
                self.assertEqual((ncl["carried_back"], ncl["closing"]), (40.0, 20.0))

    def test_which_records_are_read(self):
        from taxjson.lib.filed_locks import records_behind
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            self._project(top)
            prior = [(2023, top / "2023" / "filed" / "2023.json", "prior_year_record")]
            self.assertEqual(
                [(yr, w) for yr, _p, w in records_behind(prior, 2024, lambda p, yr: True)],
                [(2021, "opening_source"), (2022, "opening_source"),
                 (2023, "prior_year_record")])
            self.assertEqual(records_behind(prior, 2022, lambda p, yr: True), prior)
            self.assertEqual(records_behind(prior, 2024, lambda p, yr: False), prior)
            self.assertEqual(
                [yr for yr, _p, _w in records_behind(prior, 2024, lambda p, yr: yr == 2022)],
                [2022, 2023])


class TestAgainstAHandModel(unittest.TestCase):
    """The same answers whichever locks a project can read and however much of the
    earlier books it holds: the model is written apart from the fold."""
    GRID = itertools.product(
        (20.0, 50.0), (20.0, 45.0), (0.0, 30.0, 40.0), (0.0, 5.0, 20.0, 35.0, 40.0),
        (-5.0, -15.0, -30.0, 0.0, 5.0), (1.0, 0.5, 0.0))

    @rule(R.CA_CARRY_04)
    def test_the_grid(self):
        for loss22, gain, claim, entry, n24, held in self.GRID:
            used = min(gain, loss22)
            back = min(claim, loss22 - used)
            closing = loss22 - used - back
            rest = (claim - back) + max(0.0, entry - used)
            loss = max(0.0, -n24)
            back24 = min(loss, rest)
            claimed = {k: v for k, v in ((2021, claim), (2023, entry)) if v}
            nets = _nets(y2021=100.0, y2022=-loss22 * held, y2023=gain, y2024=n24)
            led = build_canada_ledger(nets, claimed, {2023: closing}, {2023: back},
                                      {2023: used}, locked_opening={2023: loss22})
            case = (loss22, gain, claim, entry, n24, held)
            self.assertEqual((_row(led, 2023)["carried_back"],
                              _row(led, 2023)["carryforward_balance"]),
                             (back, closing), case)
            self.assertEqual((_row(led, 2024)["carried_back"],
                              _row(led, 2024)["carryforward_balance"]),
                             (back24, closing + loss - back24), case)


class TestTheClose(unittest.TestCase):
    """End to end, in year folders, with and without 2023's own entry."""
    @rule(R.CA_CARRY_04)
    def test_the_lock_handoff_and_estimate_agree(self):
        for claim in ("2021 = 40.00", "2021 = 40.00, 2023 = 20.00"):
            with self.subTest(claim=claim), tempfile.TemporaryDirectory() as td:
                top = Path(td)
                _build_years(top, {2023: claim, 2024: claim},
                             {**_SALES, 2024: ("ZZQD", 470.0)}, close=(2021, 2022, 2023))
                y = top / "2024"
                est = json.loads(_ok(_cli(y, "estimate", "--json")).stdout)
                self.assertEqual(est["estimate"]["losses_carried_back"], 10.0)
                _ok(_cli(y, "close-year", "--yes"))
                ncl = json.loads((y / "filed" / "2024.json").read_text()
                                 )["carryforwards"]["net_capital_loss"]
                self.assertEqual((ncl["carried_back"], ncl["closing"]), (10.0, 20.0))
                doc = json.loads(_ok(_cli(y, "carryover", "--json")).stdout)
                r = _row(doc, 2024)
                self.assertEqual((r["carried_back"], r["carryforward_balance"]), (10.0, 20.0))
                self.assertEqual(r["carried_back_to"], {"2021": 10.0})
                _ok(_cli(y, "handoff"))


if __name__ == "__main__":
    unittest.main()
