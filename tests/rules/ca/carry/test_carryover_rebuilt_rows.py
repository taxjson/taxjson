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

from taxjson.bin.taxjson_carryover import _year_figures, build_canada_ledger


def _prior_only(nets, claimed, lock2023, opening=50.0):
    """What the 2024 project reads: 2023's lock alone."""
    back, applied, closing = lock2023
    return build_canada_ledger(nets, claimed, {2023: closing}, {2023: back},
                               {2023: applied}, locked_opening={2023: opening})


def _asked(nets, claimed, lock2023=None, opening=None, stated=None, year=2024, locks=None,
           carried_in=None, typed_in=False, typed=None, lock_stated=None, lock_to=None):
    """The ledger `tjs carryover` builds for project `year`, with what it can't
    tell: `locks` {year: (back, applied, closing)}, 2023's alone by default;
    `opening`: 2023's, when taken from the record before it ({year: opening} with
    `locks`); `stated`: [carryover] carried_back; `carried_in`: the balance the
    estimate takes into `year`, `typed_in` when it is [estimate] other_losses
    (None: the tool run alone); `typed`: {lock year: its opening, when typed in};
    `lock_stated`: {year: (the answer a lock recorded for it, that lock's year)};
    `lock_to`: ({year: (the split a lock recorded as the answer for it, that lock's
    year)}, {locked year: its own carry-back by target year}). A `stated` key
    "YEAR:TARGET" is one amount of a by-target entry."""
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
                          {y: k[1] for y, k in locks.items()}, None, opening, typed,
                          **({"lock_stated": lock_stated} if lock_stated else {}),
                          **({"lock_to": lock_to} if lock_to else {}))


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
    def test_a_balance_the_person_gave_settles_nothing_by_itself(self):
        """TJ-4886 (was TJ-4884 / TJ-4885's floor). The release check's history:
        books from 2022 (+100, a loss of 100 in 2023, +50, +30), `claimed = {
        2022 = 40 }`, the first lock 2024's, opened from [estimate] other_losses
        = 60. On the books' nets that balance says 2023 carried back 40, and a
        close with nothing asked rested on it; a T3 box 21 gain of 30 on 2023's
        return (in no net) makes it 10, and 2024's own carry-back 10 instead of
        0. So the figure is a hint beside the question, and the person answers:
        asked where the year has a loss left (2024: 10 of the 60; 2025 with a
        loss), not where it has none (2025 with a gain of 30 on 10 carried in)."""
        from taxjson.bin.taxjson_carryover import render, render_brief
        from taxjson.lib.carry_back import cant_tell_text
        nets = _nets(y2022=100.0, y2023=-100.0, y2024=50.0, y2025=30.0)
        lost = _nets(y2022=100.0, y2023=-100.0, y2024=50.0, y2025=-30.0)
        lock = {2024: (0.0, 50.0, 10.0)}
        hint = {"years": [2023], "into": 2024, "carried_in": 60.0, "books_leave": 100.0,
                "at_least": 40.0, "at_most": 40.0, "ceiling": "claims",
                "not_in_nets": ["t3_box_21", "t5_box_18_unnamed", "fx_on_cash"]}
        # (the books, the case, the project year's carry-back if 2023 carried
        # back nothing / what the books show)
        cases = ((nets, dict(year=2024, carried_in=60.0, typed_in=True), (10.0, 0.0)),
                 (lost, dict(year=2025, locks=lock, typed={2024: 60.0}, carried_in=10.0),
                  (40.0, 0.0)))
        for books, case, (if_none, if_all) in cases:
            with self.subTest(case=case):
                led = _asked(books, {2022: 40.0}, **case)
                ct = led["carry_back_cant_tell"]
                self.assertEqual((ct["years"], ct["least"], ct["books"]),
                                 ([2023], {"2023": 0.0}, {"2023": 40.0}))
                self.assertEqual((ct["if_none"]["carried_back"],
                                  ct["if_all"]["carried_back"]), (if_none, if_all))
                self.assertEqual(ct["by_books_nets"], [hint])
                self.assertNotIn("carried_back_inferred", _row(led, 2023))
                said = cant_tell_text(led)
                self.assertIn(
                    "On these books' nets alone, the 60.00 you carried into 2024 "
                    "([estimate] other_losses) would mean 2023 carried back 40.00 (the "
                    "nets leave 100.00 with nothing carried back). Not in those nets: T3 "
                    "box 21 capital gains, T5 box 18 gains not named in "
                    "[[capital_gains_dividends]] and the FX gain or loss on foreign cash "
                    "(line 15300). One of them on the return of 2023 changes that "
                    "figure, so the ledger does not take it as the answer", said)
                self.assertIn("can't tell", render_brief(led, "CAD", None))
                self.assertIn(said, " ".join(render(led, "CAD", None, True).split()))
                # The answer the hint points at, and the review's own: both taken.
                told = _asked(books, {2022: 40.0}, stated={2023: 40.0}, **case)
                self.assertNotIn("carry_back_cant_tell", told)
                self.assertNotIn("carried_in_not_in_nets", told)
                self.assertEqual((_row(told, 2023)["carried_back"],
                                  _row(told, case["year"])["carried_back"]), (40.0, 0.0))
        gain = _asked(nets, {2022: 40.0}, year=2025, locks=lock, typed={2024: 60.0},
                      carried_in=10.0)
        self.assertNotIn("carry_back_cant_tell", gain)
        self.assertNotIn("carried_back_inferred", _row(gain, 2023))

    @rule(R.CA_CARRY_04)
    def test_a_gain_on_that_return_the_nets_don_t_hold(self):
        """TJ-4886's own repro: 2022 +100, 2023 -100 in the books, a T3 box 21
        gain of 30 on 2023's return (a net loss of 70), `claimed = { 2022 = 40 }`,
        60 carried into 2024. 2023 carried back 10 (70 less the 60), which the
        ledger took for 40 without asking; said to be 10 it was refused (the
        books' 100 less 10 leaves 90, not 60). Now it asks; 10 is taken, 2024's
        loss carries the other 30 back, and the ledger says what the two figures
        need to be true: 30 of capital gains the nets don't hold."""
        from taxjson.bin.taxjson_carryover import render, render_brief
        nets = _nets(y2022=100.0, y2023=-100.0, y2024=-30.0)
        case = dict(carried_in=60.0, typed_in=True)
        ct = _asked(nets, {2022: 40.0}, **case)["carry_back_cant_tell"]
        self.assertEqual((ct["years"], ct["by_books_nets"][0]["at_least"]), ([2023], 40.0))
        code, led, said = self._said(nets, {2022: 40.0}, stated={2023: 10.0}, **case)
        self.assertIsNone(code, said)
        self.assertNotIn("carry_back_cant_tell", led)
        self.assertEqual((_row(led, 2023)["carried_back"], _row(led, 2023)[
            "carried_back_stated"], _row(led, 2024)["carried_back"]), (10.0, 10.0, 30.0))
        self.assertEqual(led["carried_in_not_in_nets"],
                         [{"into": 2024, "carried_in": 60.0, "leaves": 90.0,
                           "leaves_least": 90.0, "years": [2023],
                           "gain_not_in_nets": 30.0}])
        self.assertIn(
            "arning: your [carryover] carried_back entries leave 90.00 carried into "
            "2024 on these books' nets; you carried in 60.00 ([estimate] other_losses), "
            "30.00 less. Both hold only if the return of 2023 had 30.00 or more of "
            "capital gains that are not in these nets (T3 box 21, T5 box 18 not named "
            "in [[capital_gains_dividends]], an FX gain on foreign cash): check the "
            "entries and that balance against that return.",
            " ".join(render(led, "CAD", None, True).split()))
        self.assertIn("! 2024: carried in 60.00, 30.00 under what your carried_back "
                      "entries leave on these nets", render_brief(led, "CAD", None))
        # The estimate's split is on the balance the person carried in: 60 and
        # the year's 30, less the 30 carried back.
        from taxjson.bin.taxjson_carryover import _year_figures
        self.assertEqual(_year_figures(led, 2024, 60.0)["carryforward"], 60.0)

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
    def test_a_gain_year_with_a_loss_left_is_asked_about(self):
        """TJ-4885, the check's P1: 2023 -100, `claimed = { 2022 = 40 }`, no 2023
        lock, 2024 +80. The estimate asks whenever a loss is left, and a balance
        carried in can carry back in a gain year (TJ-1454), so "a gain year is
        never asked" closed 2024 at 0 / 20 whatever the facts. After a lock
        (2022's, carrying nothing out) 100 carried in leaves 20 to split: asked,
        the nets' reading (2023 carried back nothing: the balance is the ceiling
        too) beside it, as a hint (TJ-4886). 60 or 70 leave nothing: not asked."""
        nets = _nets(y2022=100.0, y2023=-100.0, y2024=80.0)
        lock = {2022: (0.0, 0.0, 0.0)}
        led = _asked(nets, {2022: 40.0}, locks=lock, carried_in=100.0, typed_in=True)
        ct = led["carry_back_cant_tell"]
        self.assertEqual((ct["if_none"]["carried_back"], ct["if_all"]["carried_back"],
                          ct["if_none"]["loss_left"]), (20.0, 0.0, 20.0))
        hint, = ct["by_books_nets"]
        self.assertEqual((hint["at_least"], hint["at_most"], hint["ceiling"]),
                         (0.0, 0.0, "balance"))
        for carried_in in (60.0, 70.0):
            led = _asked(nets, {2022: 40.0}, locks=lock, carried_in=carried_in,
                         typed_in=True)
            self.assertNotIn("carry_back_cant_tell", led)
            self.assertNotIn("carried_back_inferred", _row(led, 2023))
        self.assertNotIn("carry_back_cant_tell",
                         _asked(nets, {2022: 40.0}, locks=lock, carried_in=0.0))
        # Either answer is the person's and is taken. 0 is what the nets read:
        # the 20 left goes back. 40 leaves 60 on the nets, 40 under the balance:
        # refused until TJ-4886 ("the 100.00 carried into 2024 says 0.00"); a
        # capital loss of 2023 the nets don't hold makes both true, and is said.
        code, led, said = self._said(nets, {2022: 40.0}, locks=lock, carried_in=100.0,
                                     typed_in=True, stated={2023: 0.0})
        self.assertIsNone(code, said)
        self.assertEqual((_row(led, 2023)["carried_back_stated"],
                          _row(led, 2024)["carried_back"]), (0.0, 40.0))
        self.assertNotIn("carried_in_not_in_nets", led)
        code, led, said = self._said(nets, {2022: 40.0}, locks=lock, carried_in=100.0,
                                     typed_in=True, stated={2023: 40.0})
        self.assertIsNone(code, said)
        self.assertEqual(led["carried_in_not_in_nets"],
                         [{"into": 2024, "carried_in": 100.0, "leaves": 60.0,
                           "leaves_least": 60.0, "years": [2023],
                           "loss_not_in_nets": 40.0}])
        from taxjson.lib.carry_back import not_in_nets_text
        self.assertEqual(
            not_in_nets_text(led["carried_in_not_in_nets"][0]),
            "your [carryover] carried_back entries leave 60.00 carried into 2024 on "
            "these books' nets; you carried in 100.00 ([estimate] other_losses), 40.00 "
            "more. Both hold only if the return of 2023 had 40.00 of capital losses "
            "that are not in these nets (an FX loss on foreign cash): check the "
            "entries and that balance against that return")


    @rule(R.CA_CARRY_04)
    def test_with_no_lock_before_the_balance_gives_a_floor_on_the_nets_only(self):
        """The ruling of 2026-11-21: with no lock before the unchecked years, a loss
        from before the books may be in the balance typed in, so on the books' nets
        it says only the least those years carried back: what the nets leave, less
        the balance. TJ-4886: that floor is the nets', not the returns', so it is
        the hint beside the question and no reading: `least` is 0 whatever is typed.
        An entry that leaves less than the balance is taken, the rest being from
        before the books; one that leaves more is taken too, and what it needs
        (a gain the nets don't hold) is said."""
        from taxjson.bin.taxjson_carryover import render_brief
        nets = _nets(y2022=100.0, y2023=-100.0, y2024=80.0)
        ask = _asked(nets, {2022: 40.0}, carried_in=100.0, typed_in=True)
        ct = ask["carry_back_cant_tell"]
        self.assertEqual((ct["years"], ct["least"], ct["books"]),
                         ([2023], {"2023": 0.0}, {"2023": 40.0}))
        self.assertEqual((ct["if_none"]["carried_back"], ct["if_all"]["carried_back"],
                          ct["if_none"]["loss_left"]), (20.0, 0.0, 20.0))
        self.assertNotIn("by_books_nets", ct)       # (at least 0, at most the claim)
        none = _asked(nets, {2022: 40.0}, carried_in=100.0, typed_in=True,
                      stated={2023: 0.0})
        self.assertNotIn("carried_in_before_books", none)
        self.assertEqual(_row(none, 2024)["carried_back"], 40.0)    # (20 left: 20 back)
        took = _asked(nets, {2022: 40.0}, carried_in=100.0, typed_in=True,
                      stated={2023: 40.0})
        self.assertNotIn("carry_back_cant_tell", took)
        self.assertNotIn("carried_in_not_in_nets", took)
        self.assertEqual(took["carried_in_before_books"],
                         [{"into": 2024, "carried_in": 100.0, "leaves": 60.0,
                           "before_books": 40.0}])
        self.assertIn("Your [carryover] carried_back entries leave 60.00 carried into "
                      "2024; the 100.00 you carried in holds 40.00 from before these "
                      "books", " ".join(render_brief(took, "CAD", None).split()))
        # A floor short of the ceiling: 30 typed after a loss of 50, a claim of 40.
        nets = _nets(y2021=100.0, y2022=20.0, y2023=-50.0, y2024=-30.0)
        ct = _asked(nets, {2021: 40.0}, carried_in=30.0, typed_in=True)[
            "carry_back_cant_tell"]
        self.assertEqual((ct["least"], ct["books"]), ({"2023": 0.0}, {"2023": 40.0}))
        hint, = ct["by_books_nets"]
        self.assertEqual((hint["at_least"], hint["at_most"], hint["ceiling"]),
                         (20.0, 40.0, "claims"))
        from taxjson.lib.carry_back import cant_tell_text
        self.assertIn("would mean 2023 carried back at least 20.00 and at most 40.00 (the "
                      "nets leave 50.00 with nothing carried back)",
                      cant_tell_text({"carry_back_cant_tell": ct}))
        # Under the nets' floor: refused until TJ-4886, now taken and said.
        code, led, said = self._said(nets, {2021: 40.0}, carried_in=30.0, typed_in=True,
                                     stated={2023: 10.0})
        self.assertIsNone(code, said)
        self.assertEqual(led["carried_in_not_in_nets"][0]["gain_not_in_nets"], 10.0)
        self.assertEqual((_row(led, 2023)["carried_back"], _row(led, 2024)["carried_back"]),
                         (10.0, 30.0))
        # The ruling's own cases: 10 typed (the nets say 40, no more, no less) is
        # asked now, with that figure beside it; 50 typed asks as before.
        ct = _asked(nets, {2021: 40.0}, carried_in=10.0, typed_in=True)[
            "carry_back_cant_tell"]
        self.assertEqual((ct["by_books_nets"][0]["at_least"],
                          ct["by_books_nets"][0]["at_most"]), (40.0, 40.0))
        ct = _asked(nets, {2021: 40.0}, carried_in=50.0, typed_in=True)[
            "carry_back_cant_tell"]
        self.assertNotIn("by_books_nets", ct)


    @rule(R.CA_CARRY_04)
    def test_the_hint_says_what_it_rests_on(self):
        """Round 5, and TJ-4886. Two unchecked years that share a total on the
        nets (losses of 50 and 40, claims of 60, 30 typed in: 60) are named as a
        pair, "in all". After a lock the balance is the ceiling too, and is named
        as such. Either way the figure is the nets', the entry names what the nets
        leave out (`not_in_nets`), and no row is said to be settled."""
        from taxjson.lib.carry_back import cant_tell_text
        nets = _nets(y2020=100.0, y2021=-50.0, y2022=-40.0, y2023=-10.0)
        led = _asked(nets, {2020: 60.0}, year=2023, carried_in=30.0, typed_in=True)
        ct = led["carry_back_cant_tell"]
        self.assertEqual((ct["years"], ct["books"]),
                         ([2021, 2022], {"2021": 50.0, "2022": 10.0}))
        hint, = ct["by_books_nets"]
        self.assertEqual((hint["years"], hint["at_least"], hint["at_most"],
                          hint["books_leave"], hint["not_in_nets"]),
                         ([2021, 2022], 60.0, 60.0, 90.0,
                          ["t3_box_21", "t5_box_18_unnamed", "fx_on_cash"]))
        text = cant_tell_text(led)
        self.assertEqual(text.count("On these books' nets alone"), 1)
        self.assertIn("the 30.00 you carried into 2023 ([estimate] other_losses) would "
                      "mean 2021 and 2022 carried back 60.00 in all (the nets leave 90.00 "
                      "with nothing carried back)", text)
        self.assertIn("One of them on the returns of 2021 and 2022 changes that figure",
                      text)
        for year in (2021, 2022):
            self.assertNotIn("carried_back_inferred", _row(led, year))
        # A claim the project year can't reach: nothing rests on those years, so
        # nothing is asked or hinted (2024 reaches back to 2021).
        far = _nets(y2020=100.0, y2021=-50.0, y2022=-40.0, y2024=-10.0)
        self.assertNotIn("carry_back_cant_tell",
                         _asked(far, {2020: 60.0}, carried_in=30.0, typed_in=True))
        nets = _nets(y2022=100.0, y2023=-100.0, y2024=80.0)
        led = _asked(nets, {2022: 40.0}, locks={2022: (0.0, 0.0, 0.0)}, carried_in=100.0,
                     typed_in=True)
        self.assertEqual(led["carry_back_cant_tell"]["by_books_nets"][0]["ceiling"],
                         "balance")
        # An entry that is not whole is no hint (the question stands without it).
        ct = dict(led["carry_back_cant_tell"], by_books_nets=[{"years": [2023]}])
        self.assertNotIn("On these books' nets", cant_tell_text({"carry_back_cant_tell": ct}))
        self.assertIn("these books can't tell", cant_tell_text({"carry_back_cant_tell": ct}))


    @rule(R.CA_CARRY_04)
    def test_a_gain_year_between_lowers_the_nets_floor(self):
        """A gain year between (2023 +20, no `claimed` entry: what it applied is the
        taxpayer's choice, nothing to 20): other_losses = 30 after a 2022 loss of 50
        says, on the nets, that 2022 carried back at least 0: no hint. Asked, with
        a loss in 2024 (30 or 0) and with a gain of 20 there (the estimate has 10
        left: 10 or 0; the check's G2, which closed at 0 / 10). The fold's 30 is
        taken when said: 10 of the 30 carried in is then from before the books.
        After a lock the nets give 0 to 20, shown as the hint; 30 said there was
        refused, and is taken now, with the loss the nets don't hold (TJ-4886)."""
        ask = _asked(_nets(**self.STD), {2021: 30.0}, carried_in=30.0, typed_in=True)
        ct = ask["carry_back_cant_tell"]
        self.assertEqual((ct["years"], ct["books"], ct["least"]),
                         ([2022, 2023], {"2022": 30.0, "2023": 0.0},
                          {"2022": 0.0, "2023": 0.0}))
        self.assertEqual((ct["if_all"]["carried_back"], ct["if_none"]["carried_back"]),
                         (0.0, 30.0))
        self.assertNotIn("by_books_nets", ct)
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
        self.assertEqual((ct["books"], ct["least"]), ({"2022": 30.0, "2023": 0.0},
                                                      {"2022": 0.0, "2023": 0.0}))
        hint, = ct["by_books_nets"]
        self.assertEqual((hint["years"], hint["at_least"], hint["at_most"],
                          hint["ceiling"]), ([2022], 0.0, 20.0, "balance"))
        code, led, text = self._said(_nets(**self.STD), {2021: 30.0}, locks=lock,
                                     carried_in=30.0, typed_in=True,
                                     stated={2022: 30.0, 2023: 0.0})
        self.assertIsNone(code, text)
        self.assertEqual(led["carried_in_not_in_nets"],
                         [{"into": 2024, "carried_in": 30.0, "leaves": 20.0,
                           "leaves_least": 0.0, "years": [2022, 2023],
                           "loss_not_in_nets": 10.0}])
        from taxjson.lib.carry_back import not_in_nets_text
        self.assertIn("leave 20.00 carried into 2024 on these books' nets (0.00 if the "
                      "gains between took all they could); you carried in 30.00 "
                      "([estimate] other_losses), 10.00 more. Both hold only if the "
                      "returns of 2022 and 2023 had 10.00 of capital losses",
                      not_in_nets_text(led["carried_in_not_in_nets"][0]))


    @rule(R.CA_CARRY_04)
    def test_a_first_lock_s_typed_opening(self):
        """Round 1's scenario A: only 2023 closed, on other_losses = 50, after a
        2022 loss of 50: asked (as is a lock opened from nothing). After a lock
        the nets say 2022 carried back nothing, and the claim of 30 was 2024's
        with no question; that is the hint now, and 0 the answer it points at
        (TJ-4886)."""
        lock = {2023: (0.0, 20.0, 30.0)}
        led = _asked(_nets(**self.STD), {2021: 30.0}, locks=lock, typed={2023: 50.0},
                     carried_in=30.0)
        self.assertEqual(led["carry_back_cant_tell"]["years"], [2022])
        none = _asked(_nets(**self.STD), {2021: 30.0}, locks={2023: (0.0, 0.0, 0.0)},
                      carried_in=0.0)
        self.assertEqual(none["carry_back_cant_tell"]["years"], [2022])
        both = {2021: (0.0, 0.0, 0.0), **lock}
        led = _asked(_nets(**self.STD), {2021: 30.0}, typed={2023: 50.0}, carried_in=30.0,
                     locks=both)
        hint, = led["carry_back_cant_tell"]["by_books_nets"]
        self.assertEqual((hint["years"], hint["into"], hint["carried_in"],
                          hint["at_least"], hint["at_most"], hint["ceiling"]),
                         ([2022], 2023, 50.0, 0.0, 0.0, "balance"))
        led = _asked(_nets(**self.STD), {2021: 30.0}, typed={2023: 50.0}, carried_in=30.0,
                     locks=both, stated={2022: 0.0})
        self.assertNotIn("carry_back_cant_tell", led)
        self.assertEqual((_split(led, 2022), _split(led)), ((0.0, 50.0), (30.0, 30.0)))
        self.assertNotIn("carried_back_inferred", _row(led, 2022))

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
    def _years(self, top, last_sale, other_losses, answer=""):
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
                toml + "\n[carryover]\n" + (answer if year == 2024 else "")
                + "claimed = { 2022 = 40.00 }\n")
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
    def test_a_typed_balance_is_asked_about_once_and_gain_years_close(self):
        """With nothing typed a gain year has nothing left to split and closes
        unasked. With 60 typed, 2024 (+50) has 10 left: whether it goes back
        depends on what 2023 carried back, which the 60 says only on the books'
        nets (TJ-4886; it closed unasked on that figure). Asked once, the nets'
        figure beside the question; answered, 2024 closes and records the answer,
        and 2025, a gain year, closes unasked."""
        for other_losses, into_2025 in ((60, 10.0), (None, 0.0)):
            with self.subTest(other_losses=other_losses), \
                    tempfile.TemporaryDirectory() as td:
                d24, d25 = self._years(Path(td), 530.0, other_losses)
                _ok(_cli(d24, "run", "--no-input"))
                asked = self._says(d24)
                self.assertEqual(asked, other_losses == 60)
                if asked:
                    closed = _cli(d24, "close-year", "--yes")
                    self.assertEqual(closed.returncode, 1, closed.stdout + closed.stderr)
                    said = " ".join((closed.stdout + closed.stderr).split())
                    self.assertIn("add `carried_back = { 2023 = 40.00 }` if it did", said)
                    self.assertIn("On these books' nets alone, the 60.00 you carried "
                                  "into 2024 ([estimate] other_losses) would mean 2023 "
                                  "carried back 40.00", said)
                    self.assertFalse((d24 / "filed" / "2024.json").exists())
                    toml = d24 / "taxjson.toml"
                    toml.write_text(toml.read_text().replace(
                        "claimed =", "carried_back = { 2023 = 40.00 }\nclaimed ="))
                    self.assertFalse(self._says(d24))
                _ok(_cli(d24, "close-year", "--yes"))
                ncl = self._ncl(d24)
                self.assertEqual((ncl.get("carried_back", 0.0), ncl["closing"]),
                                 (0.0, into_2025))
                self.assertEqual(ncl.get("carry_back_stated"),
                                 {"2023": 40.0} if asked else None)
                _ok(_cli(d25, "run", "--no-input"))
                self.assertFalse(self._says(d25))
                _ok(_cli(d25, "handoff"))
                _ok(_cli(d25, "close-year", "--yes"))
                ncl = self._ncl(d25)
                self.assertEqual((ncl["opening"], ncl.get("carried_back", 0.0),
                                  ncl["closing"]), (into_2025, 0.0, 0.0))

    @rule(R.CA_CARRY_04)
    def test_a_loss_year_after_it(self):
        """With the 60 typed in and 2024 closed on the answer (2023 carried back
        40), 2025's project reads that answer in 2024's lock: its loss is simply
        carried forward, with no second question, its ledger says whose figure
        2023's is, and its own lock keeps it. An entry there that says otherwise
        is refused, naming both. With nothing typed, 2024 closed unasked and
        recorded no answer: all three ask in 2025, once."""
        with tempfile.TemporaryDirectory() as td:
            d24, d25 = self._years(Path(td), 470.0, 60,
                                   "carried_back = { 2023 = 40.00 }\n")
            _ok(_cli(d24, "run", "--no-input"))
            _ok(_cli(d24, "close-year", "--yes"))
            self.assertEqual(self._ncl(d24)["carry_back_stated"], {"2023": 40.0})
            _ok(_cli(d25, "run", "--no-input"))
            self.assertFalse(self._says(d25))
            led = json.loads(_ok(_cli(d25, "carryover", "--json")).stdout)
            self.assertEqual((_row(led, 2023)["carried_back_stated"],
                              _row(led, 2023)["carried_back_stated_in_lock"]), (40.0, 2024))
            self.assertIn("2023: carried back 40.00, your figure ([carryover] "
                          "carried_back, as 2024's close-year lock recorded it), not "
                          "these books'", " ".join(_ok(_cli(d25, "carryover")).stdout.split()))
            toml = d25 / "taxjson.toml"
            kept = toml.read_text()
            toml.write_text(kept.replace("claimed =", "carried_back = { 2023 = 0 }\nclaimed ="))
            other = _cli(d25, "carryover")
            self.assertEqual(other.returncode, 1, other.stdout + other.stderr)
            self.assertIn("[carryover] carried_back says 2023 carried back 0.00, but "
                          "2024's close-year lock records your answer 40.00 for it "
                          "(carry_back_stated)", " ".join(other.stderr.split()))
            toml.write_text(kept)
            _ok(_cli(d25, "close-year", "--yes"))
            ncl = self._ncl(d25)
            self.assertEqual((ncl["opening"], ncl.get("carried_back", 0.0), ncl["closing"]),
                             (10.0, 0.0, 40.0))
            self.assertEqual(ncl["carry_back_stated"], {"2023": 40.0})
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
    def test_the_balance_typed_in_is_asked_about_where_a_loss_is_left(self):
        """One folder (2022's lock is read) or year folders (no lock is read): 100
        carried in leaves 20 of it after 2024's gain, and whether that goes back
        rests on what 2023 carried back. On the nets the 100 says nothing (one
        folder), or at least nothing (year folders); neither is taken (TJ-4886:
        one folder closed at 20 / 0 unasked): asked, and said to be nothing, 20
        goes back. Nothing typed, or 60, leaves nothing to split: not asked."""
        # other_losses: (2023's carry-back, the lock's applied / carried back / closing)
        want = {None: (40.0, (0.0, 0.0, 0.0)), 100: (0.0, (80.0, 20.0, 0.0)),
                60: (40.0, (60.0, 0.0, 0.0))}
        for single in (False, True):
            for other_losses, (back23, lock) in want.items():
                with self.subTest(single=single, other_losses=other_losses), \
                        tempfile.TemporaryDirectory() as td:
                    d = self._project(Path(td), single, other_losses)
                    asked, est, led = self._agree(d)
                    self.assertEqual(asked, other_losses == 100)
                    if asked:
                        self.assertEqual(
                            "by_books_nets" in led["carry_back_cant_tell"], single)
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
                    self.assertNotIn("carried_back_inferred", _row(led, 2023))
                    self.assertNotIn("carried_in_not_in_nets", led)
                    self.assertEqual((est["losses_carried_back"],
                                      est["losses_carried_forward"]), lock[1:])
                    _ok(_cli(d, "close-year", "--yes"))
                    ncl = self._ncl(d)
                    self.assertEqual((ncl["applied"], ncl.get("carried_back", 0.0),
                                      ncl["closing"]), lock)

    @rule(R.CA_CARRY_04)
    def test_an_entry_against_the_balance(self):
        """After a lock, 100 carried in and "2023 carried back 40" don't add up on
        the nets (they leave 60): it was refused, and an FX loss on cash in 2023
        makes both true (TJ-4886). The entry is taken, and the ledger, the
        estimate and the close say what the two figures need; the lock records
        the answer. With no lock before, an entry that leaves less than the
        balance is taken as before: the rest was carried in from before the
        books, and is said."""
        with tempfile.TemporaryDirectory() as td:
            d = self._project(Path(td), True, 100)
            toml = d / "taxjson.toml"
            toml.write_text(toml.read_text().replace(
                "claimed =", "carried_back = { 2023 = 40 }\nclaimed ="))
            asked, est, led = self._agree(d)
            self.assertFalse(asked)
            self.assertEqual(led["carried_in_not_in_nets"],
                             [{"into": 2024, "carried_in": 100.0, "leaves": 60.0,
                               "leaves_least": 60.0, "years": [2023],
                               "loss_not_in_nets": 40.0}])
            self.assertEqual(est["carried_in_not_in_nets"], led["carried_in_not_in_nets"])
            self.assertIn("! 2024: carried in 100.00, 40.00 over what your carried_back "
                          "entries leave on these nets", _ok(_cli(d, "carryover")).stdout)
            why = ("your [carryover] carried_back entries leave 60.00 carried into 2024 "
                   "on these books' nets; you carried in 100.00 ([estimate] "
                   "other_losses), 40.00 more. Both hold only if the return of 2023 had "
                   "40.00 of capital losses that are not in these nets (an FX loss on "
                   "foreign cash)")
            self.assertIn("arning: " + why, " ".join(
                _ok(_cli(d, "carryover", "--details")).stdout.split()))
            for cmd in (("estimate",), ("close-year", "--yes")):
                res = _ok(_cli(d, *cmd))
                self.assertIn("the balance carried in and [carryover] carried_back "
                              "differ on these books' nets (`tjs carryover`): " + why,
                              " ".join(res.stderr.split()), cmd)
            ncl = self._ncl(d)
            self.assertEqual((ncl["applied"], ncl.get("carried_back", 0.0), ncl["closing"]),
                             (80.0, 0.0, 20.0))
            self.assertEqual(ncl["carry_back_stated"], {"2023": 40.0})
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
            d = self._project(Path(td), True, 100, extra="carried_back = { 2023 = 0 }\n")
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


class TestWhichClaimedYearACarryBackWentTo(unittest.TestCase):
    """TJ-4882: a T1A names the years a loss goes back to, and `claimed` is one
    total per target year, a lock's `carried_back` or a plain [carryover]
    carried_back amount one total per loss year. With claims under two earlier
    years in reach the ledger meets the oldest first, a proposal. Where the
    project year's carry-back, the loss it carries forward or the years its own
    carry-back goes to change with that order, the ledger can't tell, the
    estimate's carry-forward is unknown and `tjs close-year` stops, until a
    by-target entry (`carried_back = { 2022 = { 2021 = 40.00 } }`) or a lock's
    `carried_back_to` says which. Every reading of the open years is folded
    (each order of the claimed years in reach; an unchecked year cut anywhere
    down to nothing; one year this way and another that), and one the locks
    rule out is dropped: every line the stop offers is one the ledger takes.
    The worked example: books 2020 +100, 2021 +100, 2022 -40, 2024 -40, claims
    of 40 under 2020 and 2021."""
    NETS = dict(y2020=100.0, y2021=100.0, y2022=-40.0, y2024=-40.0)
    CLAIMS = {2020: 40.0, 2021: 40.0}
    OLDEST = {"to": {"2022": {"2020": 40.0}}, "carried_back": 40.0,
              "carryforward": 0.0, "carried_back_to": {"2021": 40.0}}
    LATEST = {"to": {"2022": {"2021": 40.0}}, "carried_back": 0.0,
              "carryforward": 40.0, "carried_back_to": {}}

    def _led(self, stated=None, nets=None, claims=None, **case):
        case.setdefault("carried_in", 0.0)
        return _asked(_nets(**(nets or self.NETS)), claims or self.CLAIMS,
                      stated=stated, **case)

    def _refused(self, stated=None, **case):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stop:
            self._led(stated, **case)
        self.assertEqual(stop.exception.code, 1)
        return " ".join(err.getvalue().split())

    def _figures(self, led, year=2024):
        from taxjson.bin.taxjson_carryover import _year_figures
        fig = _year_figures(led, year, 0.0)
        return fig["carried_back"], fig["carryforward"]

    @rule(R.CA_CARRY_04)
    def test_the_worked_example(self):
        """No entry: 2022 is rebuilt, and whether it carried 40 back to 2020, 40
        back to 2021 or nothing decides what 2024 carries back (2020's claim is
        out of 2024's reach). The ledger can't tell, and gives the line for each
        reading with the figures it gives: one line for each set of figures
        (nothing carried back gives the first's)."""
        from taxjson.bin.taxjson_carryover import render, render_brief
        from taxjson.lib.carry_back import cant_tell_text
        led = self._led()
        self.assertNotIn("carry_back_cant_tell", led)
        self.assertEqual(led["carry_back_split_cant_tell"],
                         {"years": [2022], "unchecked": [2022], "answered": {},
                          "readings": [self.OLDEST, self.LATEST]})
        said = cant_tell_text(led)
        self.assertIn("these books can't tell which [carryover] claimed years the "
                      "carry-back of 2022 went to, with more than one in reach, or "
                      "whether it was made: a T1A names them, and no lock or "
                      "[carryover] carried_back entry records them.", said)
        self.assertIn("add `carried_back = { 2022 = { 2020 = 40.00 } }` if 2022 "
                      "carried back 40.00 to 2020 (this year then carries back "
                      "40.00 to 2021 and 0.00 forward); or `carried_back = { 2022 = "
                      "{ 2021 = 40.00 } }` if 2022 carried back 40.00 to 2021 "
                      "(this year then carries back 0.00 and 40.00 forward); or the "
                      "amounts its T1A did carry back, under the years it names",
                      said)
        brief = render_brief(led, "CAD", None)
        self.assertIn("Net-capital-loss carryforward after 2024: can't tell (0.00 if "
                      "2022 carried back 40.00 to 2020, 40.00 if 2022 carried back "
                      "40.00 to 2021)", brief)
        self.assertIn("T1A carry-back of 2024's loss: can't tell (40.00 to 2021 if",
                      brief)
        self.assertIn("! can't tell which claimed years 2022 carried back to (T1A): "
                      "set [carryover] carried_back by year", brief)
        full = " ".join(render(led, "CAD", None, True).split())
        self.assertIn("arning: " + said + ". The rows above are working, not an "
                      "answer. In taxjson.toml, `[carryover] carried_back = { YEAR = "
                      "{ TARGET = AMOUNT } }`", full)

    @rule(R.CA_CARRY_04)
    def test_a_by_target_entry_says_it(self):
        """`{ 2022 = { 2021 = 40.00 } }`: nothing back, 40 forward. `{ 2022 = {
        2020 = 40.00 } }`: 40 back, nothing forward. `{ 2022 = 0 }` settles it too
        (it was noted as not needed: the question compared the oldest first
        only). A plain total says how much, not where: the question stays."""
        from taxjson.bin.taxjson_carryover import render_brief
        latest = self._led({"2022:2021": 40.0})
        self.assertNotIn("carry_back_split_cant_tell", latest)
        self.assertEqual(self._figures(latest), (0.0, 40.0))
        row = _row(latest, 2022)
        self.assertEqual((row["carried_back_to"], row["carried_back_to_said"],
                          row["carried_back_stated"], "carried_back_split" in row),
                         ({"2021": 40.0}, "entry", 40.0, False))
        self.assertIn("2022: carried back 40.00 (to 2021 40.00), your figure "
                      "([carryover] carried_back), not these books'",
                      " ".join(render_brief(latest, "CAD", None).split()))
        oldest = self._led({"2022:2020": 40.0})
        self.assertNotIn("carry_back_split_cant_tell", oldest)
        self.assertEqual((self._figures(oldest), _row(oldest, 2024)["carried_back_to"]),
                         ((40.0, 0.0), {"2021": 40.0}))
        none = self._led({2022: 0.0})
        self.assertNotIn("carry_back_split_cant_tell", none)
        self.assertEqual((self._figures(none), _row(none, 2022)["carried_back_stated"]),
                         ((40.0, 0.0), 0.0))
        total = self._led({2022: 40.0})
        ct = total["carry_back_split_cant_tell"]
        self.assertEqual((ct["years"], ct["unchecked"], ct["answered"],
                          [r["to"] for r in ct["readings"]]),
                         ([2022], [], {"2022": 40.0},
                          [{"2022": {"2020": 40.0}}, {"2022": {"2021": 40.0}}]))
        self.assertEqual(_row(total, 2022)["carried_back_split"], "oldest_first")
        # Several years: 60 lost in 2022, 40 back to 2020 and 20 to 2021, or 20 and 40.
        nets = dict(self.NETS, y2022=-60.0)
        both = self._led({"2022:2020": 40.0, "2022:2021": 20.0}, nets=nets)
        self.assertEqual((_row(both, 2022)["carried_back_to"], self._figures(both)),
                         ({"2020": 40.0, "2021": 20.0}, (20.0, 20.0)))
        other = self._led({"2022:2020": 20.0, "2022:2021": 40.0}, nets=nets)
        self.assertEqual((_row(other, 2022)["carried_back_to"], self._figures(other)),
                         ({"2020": 20.0, "2021": 40.0}, (0.0, 40.0)))

    @rule(R.CA_CARRY_04)
    def test_an_entry_that_can_t_be_is_refused_by_name(self):
        """More to a year than its claim had left; a year with no claim; the tool's
        own line checked as the config's is (a year out of the 3 before it, a
        negative amount, a year twice)."""
        self.assertIn(
            "[carryover] carried_back says 2022 carried 50.00 back to 2020, more than "
            "the 40.00 of the [carryover] claimed amount under 2020 that a 2022 loss "
            "could meet (what the carry-backs before it left of it): correct the entry "
            "for 2022, the one for an earlier year, or `claimed`, in taxjson.toml",
            self._refused({"2022:2020": 50.0}))
        self.assertIn("says 2022 carried 40.00 back to 2019, more than the 0.00 of the "
                      "[carryover] claimed amount under 2019",
                      self._refused({"2022:2019": 40.0}))
        # 2023 lost 30 too, and is said to have gone to 2021, where 2022 left 10.
        nets = dict(self.NETS, y2023=-30.0)
        self.assertIn("says 2023 carried 30.00 back to 2021, more than the 10.00 of the "
                      "[carryover] claimed amount under 2021",
                      self._refused({"2022:2021": 30.0, "2023:2021": 30.0}, nets=nets))
        import argparse
        import contextlib
        import io
        from taxjson.bin.taxjson_carryover import _stated_backs
        self.assertEqual(
            _stated_backs(argparse.Namespace(carried_back_year=[
                "2022:2020=10.0", "2022:2021=30.0", "2023=0.0"])),
            ({2022: 40.0, 2023: 0.0}, {2022: {2020: 10.0, 2021: 30.0}}))
        for bad in (["2022:2018=5.0"], ["2022:2022=5.0"], ["2022:2021=-1.0"],
                    ["2022:2021=nan"], ["2022:2021=5.0", "2022:2021=5.0"],
                    ["2022=5.0", "2022:2021=5.0"], ["2022:2021=5.0", "2022=5.0"],
                    ["2022:x=5.0"]):
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stop:
                _stated_backs(argparse.Namespace(carried_back_year=bad))
            self.assertEqual(stop.exception.code, 2, bad)
            self.assertIn("YEAR:TARGET=AMOUNT", err.getvalue())

    @rule(R.CA_CARRY_04)
    def test_the_config_checks_every_year_and_amount(self):
        from taxjson.lib.project_tables import carried_back_entries, carried_back_stated
        claimed = {"2020": 40.0, "2021": 40.0}

        def read(entry):
            return carried_back_entries({"carryover": {"claimed": claimed,
                                                       "carried_back": entry}})
        self.assertEqual(read({"2022": {"2020": 10.0, "2021": 30}, "2023": 0}),
                         ({2022: 40.0, 2023: 0.0}, {2022: {2020: 10.0, 2021: 30.0}}, []))
        self.assertEqual(carried_back_stated({"carryover": {
            "claimed": claimed, "carried_back": {"2022": {"2021": 40.0}}}}),
            ({2022: 40.0}, []))
        nan = float("nan")
        for entry, why in (
                ({"2022": {"2018": 5}}, "2022 = { 2018 = 5 }: 2018 is not one of the 3 "
                                        "years before 2022 (2019 to 2021)"),
                ({"2022": {"2022": 5}}, "2022 is not one of the 3 years before 2022"),
                ({"2022": {"2021": -1}}, "2021 must be an amount >= 0"),
                ({"2022": {"2021": "40"}}, "2021 must be an amount >= 0"),
                ({"2022": {"2021": nan}}, "2021 must be an amount >= 0"),
                ({"2022": {"2021": True}}, "2021 must be an amount >= 0"),
                ({"2022": {"x": 5}}, "'x' is not a tax year"),
                ({"2022": {}}, "names no year"),
                ({"2022": {"2021": 50}}, "50.00 carried back to 2021, but [carryover] "
                                         "claimed has 40.00 under 2021"),
                ({"2022": {"2019": 5}}, "5.00 carried back to 2019, but [carryover] "
                                        "claimed has no amount under 2019"),
                ({"x": {"2021": 5}}, "'x' is not a tax year"),
                ({"2022": {"2021": 5}, "02022": 5}, "2022 is given twice")):
            totals, to, problems = read(entry)
            self.assertEqual((totals, to), ({}, {}), entry)
            self.assertEqual(len(problems), 1, problems)
            self.assertIn("[carryover] carried_back: ", problems[0])
            self.assertIn(why, problems[0])
        # One bad entry leaves the good ones read.
        totals, to, problems = read({"2022": {"2021": 40.0}, "2023": {"2019": 1}})
        self.assertEqual((totals, to, len(problems)), ({2022: 40.0}, {2022: {2021: 40.0}}, 1))

    @rule(R.CA_CARRY_04)
    def test_a_lock_s_total_does_not_say_the_years_and_its_carried_back_to_does(self):
        """2022 closed before the lock recorded the years (`carried_back` 40, no
        `carried_back_to`): read as now, the oldest first, and asked about where
        it matters; a by-target entry for it answers, its amounts adding up to the
        lock's. A lock that records the years is the record: nothing is asked."""
        old = dict(locks={2022: (40.0, 0.0, 0.0)})
        ct = self._led(**old)["carry_back_split_cant_tell"]
        self.assertEqual((ct["years"], ct["unchecked"], ct["readings"]),
                         ([2022], [], [self.OLDEST, self.LATEST]))
        self.assertNotIn("more", ct)
        told = self._led({"2022:2021": 40.0}, **old)
        self.assertNotIn("carry_back_split_cant_tell", told)
        self.assertEqual((self._figures(told), _row(told, 2022)["carried_back_to_said"]),
                         ((0.0, 40.0), "entry"))
        self.assertNotIn("carried_back_stated", _row(told, 2022))   # (the lock's amount)
        self.assertIn("[carryover] carried_back says 2022 carried back 30.00 to 2021, "
                      "but 2022's close-year lock records 40.00: the entry says which "
                      "years that went to; make its amounts add up to it",
                      self._refused({"2022:2021": 30.0}, **old))
        # A total that repeats the lock's says nothing new: the question stays.
        self.assertIn("carry_back_split_cant_tell", self._led({2022: 40.0}, **old))
        new = dict(old, lock_to=({}, {2022: {2021: 40.0}}))
        led = self._led(**new)
        self.assertNotIn("carry_back_split_cant_tell", led)
        row = _row(led, 2022)
        self.assertEqual((self._figures(led), row["carried_back_to"],
                          row["carried_back_to_said"], row["carried_back_to_in_lock"]),
                         ((0.0, 40.0), {"2021": 40.0}, "lock", 2022))
        self.assertIn("[carryover] carried_back says 2022 carried back 40.00 to 2020, "
                      "but 2022's close-year lock records 40.00 to 2021 "
                      "(carried_back_to): remove that entry",
                      self._refused({"2022:2020": 40.0}, **new))
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            same = self._led({"2022:2021": 40.0}, **new)
        self.assertEqual(same, led)
        self.assertIn("entry for 2022 is no longer needed: 2022's close-year lock "
                      "records the same", " ".join(err.getvalue().split()))

    @rule(R.CA_CARRY_04)
    def test_the_first_question_names_the_years_where_they_matter(self):
        """A loss of 60 in 2022: whether it carried back at all decides 2024's
        carry-back (the question of TJ-1574), and the line for "it did" names
        the years, the oldest first. A total answers how much; where it went is
        asked next; the by-target line answers both at once."""
        from taxjson.lib.carry_back import cant_tell_text
        nets = dict(self.NETS, y2022=-60.0)
        ct = self._led(nets=nets)["carry_back_cant_tell"]
        self.assertEqual((ct["years"], ct["if_all"]["carried_back"],
                          ct["if_none"]["carried_back"], ct["books_to"]),
                         ([2022], 20.0, 40.0, {"2022": {"2020": 40.0, "2021": 20.0}}))
        said = cant_tell_text({"carry_back_cant_tell": ct})
        self.assertIn("add `carried_back = { 2022 = { 2020 = 40.00, 2021 = 20.00 } }` "
                      "if it did, as the books read (this year then carries back "
                      "20.00), or `carried_back = { 2022 = 0 }` if not (then 40.00)",
                      said)
        self.assertIn("Where the line names the years the loss went back to, it is "
                      "the oldest claimed year first: write the years and amounts its "
                      "T1A names", said)
        total = self._led({2022: 60.0}, nets=nets)
        self.assertNotIn("carry_back_cant_tell", total)
        ends = total["carry_back_split_cant_tell"]["readings"]
        self.assertEqual([(r["to"]["2022"], r["carried_back"], r["carryforward"])
                          for r in ends],
                         [({"2020": 40.0, "2021": 20.0}, 20.0, 20.0),
                          ({"2020": 20.0, "2021": 40.0}, 0.0, 40.0)])
        self.assertNotIn("carry_back_split_cant_tell",
                         self._led({2022: 0.0}, nets=nets))

    @rule(R.CA_CARRY_04)
    def test_nothing_is_asked_where_nothing_rests_on_the_order(self):
        """One claimed year in reach, or every claim in reach met: no other
        split was open. And a split that was open, with the project year out of
        reach of what it left: its row says the split is the ledger's, and
        nothing is asked."""
        one = self._led(nets=dict(y2021=100.0, y2022=-40.0, y2024=-40.0),
                        claims={2021: 80.0})
        every = self._led(nets=dict(self.NETS, y2022=-80.0),
                          locks={2022: (80.0, 0.0, 0.0)})
        for led in (one, every):
            self.assertNotIn("carry_back_split_cant_tell", led)
            self.assertNotIn("carry_back_cant_tell", led)
            self.assertFalse([r for r in led["rows"] if "carried_back_split" in r])
        far = self._led(nets=dict(y2020=100.0, y2021=100.0, y2022=-40.0, y2025=-40.0),
                        locks={2022: (40.0, 0.0, 0.0)}, year=2025)
        self.assertNotIn("carry_back_split_cant_tell", far)
        self.assertEqual((_row(far, 2022)["carried_back_split"],
                          _row(far, 2025)["carried_back"]), ("oldest_first", 0.0))
        # A gain year has nothing to split.
        gain = self._led(nets=dict(self.NETS, y2024=40.0))
        self.assertNotIn("carry_back_split_cant_tell", gain)

    @rule(R.CA_CARRY_04)
    def test_the_years_this_year_s_carry_back_goes_to_are_a_figure_too(self):
        """2023 loses 40 after 2022's 40: it carries 40 back whichever claim 2022
        met, to the other one. The lock records the years, the T1A names them and
        `tjs handoff` compares by year, so the ledger can't tell here either
        (the first step said nothing: the amounts agree)."""
        nets = dict(y2020=100.0, y2021=100.0, y2022=-40.0, y2023=-40.0)
        led = self._led(nets=nets, locks={2022: (40.0, 0.0, 0.0)}, year=2023)
        ends = led["carry_back_split_cant_tell"]["readings"]
        self.assertEqual([(r["carried_back"], r["carryforward"], r["carried_back_to"])
                          for r in ends],
                         [(40.0, 0.0, {"2021": 40.0}), (40.0, 0.0, {"2020": 40.0})])
        from taxjson.bin.taxjson_carryover import render_brief
        brief = render_brief(led, "CAD", None)
        self.assertIn("Net-capital-loss carryforward after 2023: 0.00", brief)
        self.assertIn("T1A carry-back of 2023's loss: can't tell (40.00 to 2021 if 2022 "
                      "carried back 40.00 to 2020, 40.00 to 2020 if 2022 carried back "
                      "40.00 to 2021)", brief)
        told = self._led({"2022:2021": 40.0}, nets=nets, locks={2022: (40.0, 0.0, 0.0)},
                         year=2023)
        self.assertEqual(_row(told, 2023)["carried_back_to"], {"2020": 40.0})

    @rule(R.CA_CARRY_04)
    def test_the_project_year_s_own_t1a(self):
        """2024 loses 60 with 40 claimed under each of 2021 and 2022: the ledger
        proposes the oldest first (40 and 20) and says it is a proposal. A
        by-target entry for the project year says another split of the same 60;
        one that adds up to another amount, or a plain total, is refused."""
        from taxjson.bin.taxjson_carryover import render, render_brief
        nets = dict(y2021=100.0, y2022=100.0, y2024=-60.0)
        claims = {2021: 40.0, 2022: 40.0}
        led = self._led(nets=nets, claims=claims)
        row = _row(led, 2024)
        self.assertEqual((row["carried_back"], row["carried_back_to"],
                          row["carried_back_split"], "carried_back_to_said" in row),
                         (60.0, {"2021": 40.0, "2022": 20.0}, "oldest_first", False))
        self.assertIn("! 2024: T1A carry-back by year, oldest first: 2021 40.00, 2022 "
                      "20.00 (a proposal)", render_brief(led, "CAD", None))
        self.assertIn("- 2024: its carry-back is split between the --claimed years in "
                      "reach oldest first (2021 40.00, 2022 20.00), the ledger's "
                      "proposal for its T1A, which `tjs close-year` records. If the "
                      "T1A names other years, say so: `carried_back = { 2024 = { YEAR "
                      "= AMOUNT } }` under [carryover].",
                      " ".join(render(led, "CAD", None, True).split()))
        mine = self._led({"2024:2021": 20.0, "2024:2022": 40.0}, nets=nets, claims=claims)
        row = _row(mine, 2024)
        self.assertEqual((row["carried_back"], row["carried_back_to"],
                          row["carried_back_to_said"], "carried_back_split" in row),
                         (60.0, {"2021": 20.0, "2022": 40.0}, "entry", False))
        self.assertIn("2024: its carry-back went to 2021 20.00, 2022 40.00, your "
                      "figures ([carryover] carried_back)",
                      " ".join(render_brief(mine, "CAD", None).split()))
        self.assertIn(
            "[carryover] carried_back says 2024 carries back 50.00 to 2021 (10.00) and "
            "2022 (40.00), but 2024 carries back 60.00: the [carryover] claimed amounts "
            "under the 3 years before it that no earlier loss met, as far as the loss it "
            "has left goes. The entry for the project year says which years that goes "
            "to: make its amounts add up to 60.00 in taxjson.toml",
            self._refused({"2024:2021": 10.0, "2024:2022": 40.0}, nets=nets,
                          claims=claims))
        self.assertIn("names 2024, not a year before the project year",
                      self._refused({2024: 60.0}, nets=nets, claims=claims))
        self.assertIn("names 2025, not a year before the project year",
                      self._refused({"2025:2022": 5.0}, nets=nets, claims=claims))
        # The estimate's loss left caps it: 30 carried in less a gain of 10 leaves 20.
        gain = dict(nets=dict(y2021=100.0, y2022=100.0, y2024=10.0), claims=claims,
                    carried_in=30.0, locks={2023: (0.0, 0.0, 30.0)})
        self.assertIn("but 2024 carries back 20.00",
                      self._refused({"2024:2021": 30.0}, **gain))

    def _offered(self, led):
        """Each line the ledger offers, as the --carried-back-year items the
        wrapper would pass for it (the entries in use kept)."""
        ct = led["carry_back_split_cant_tell"]
        for r in ct["readings"]:
            said = {}
            for y, v in {**ct["answered"], **r["to"]}.items():
                if isinstance(v, dict):
                    said.update({f"{y}:{t}": a for t, a in v.items()} or {int(y): 0.0})
                else:
                    said[int(y)] = v
            yield r, said

    @rule(R.CA_CARRY_04)
    def test_a_middle_year_of_three(self):
        """The review's P1-1: 60, 40 and 40 claimed under 2020 to 2022; 2023
        closed with 20 carried back and no years on record. Met the oldest year
        first or the latest, 2024 carries its 40 back to 2021; had 2023's T1A
        named 2021, the middle one, only 20 of that claim is left and 2024's
        goes to 2021 and 2022. Nothing asked, and the entry that says so was
        set aside as not needed: the lock recorded years no return had."""
        case = dict(nets=dict(y2020=100.0, y2021=100.0, y2022=100.0, y2023=-20.0,
                              y2024=-40.0),
                    claims={2020: 60.0, 2021: 40.0, 2022: 40.0},
                    locks={2023: (20.0, 0.0, 0.0)})
        led = self._led(**case)
        ct = led["carry_back_split_cant_tell"]
        self.assertEqual([(r["to"], r["carried_back"], r["carried_back_to"])
                          for r in ct["readings"]],
                         [({"2023": {"2020": 20.0}}, 40.0, {"2021": 40.0}),
                          ({"2023": {"2021": 20.0}}, 40.0, {"2021": 20.0, "2022": 20.0})])
        told = self._led({"2023:2021": 20.0}, **case)
        self.assertNotIn("carry_back_split_cant_tell", told)
        self.assertEqual((_row(told, 2024)["carried_back_to"],
                          _row(told, 2023)["carried_back_to_said"]),
                         ({"2021": 20.0, "2022": 20.0}, "entry"))
        for _r, said in self._offered(led):
            self.assertNotIn("carry_back_split_cant_tell", self._led(said, **case))

    @rule(R.CA_CARRY_04)
    def test_one_year_as_the_books_show_and_another_nothing(self):
        """The review's P1-2 (as on v1.1.0): no lock; 2020 and 2022 both
        unchecked. Both carried back what the books show, or both nothing: 2024
        carries back nothing either way, and nothing was asked. But 2020 as the
        books show and 2022 nothing leaves 2021's claim for 2024: 40 back, 20
        forward. Every combination is folded now; the entry is taken."""
        case = dict(nets=dict(y2018=60.0, y2020=-60.0, y2021=100.0, y2022=-80.0,
                              y2024=-60.0), claims={2018: 40.0, 2021: 60.0})
        led = self._led(**case)
        self.assertNotIn("carry_back_cant_tell", led)
        ct = led["carry_back_split_cant_tell"]
        self.assertEqual((ct["years"], ct["unchecked"]), ([2020, 2022], [2020, 2022]))
        self.assertEqual([(r["to"], r["carried_back"], r["carryforward"])
                          for r in ct["readings"]],
                         [({"2020": {"2018": 40.0}, "2022": {"2021": 40.0}}, 0.0, 60.0),
                          ({"2020": {"2018": 40.0}, "2022": {}}, 40.0, 20.0)])
        from taxjson.lib.carry_back import cant_tell_text
        self.assertIn("`carried_back = { 2020 = { 2018 = 40.00 }, 2022 = 0 }` if 2020 "
                      "carried back 40.00 to 2018 and 2022 carried nothing back (this "
                      "year then carries back 40.00 to 2021 and 20.00 forward)",
                      cant_tell_text(led))
        for said in ({2020: 40.0, 2022: 0.0}, {"2020:2018": 40.0, 2022: 0.0}):
            told = self._led(said, **case)
            self.assertNotIn("carry_back_split_cant_tell", told)
            self.assertNotIn("carry_back_cant_tell", told)
            self.assertEqual(self._figures(told), (40.0, 20.0))
        # One of the two answers: taken, and the other year is asked about.
        half = self._led({2022: 0.0}, **case)
        self.assertEqual(_row(half, 2022)["carried_back_stated"], 0.0)
        self.assertIn(2020, half["carry_back_cant_tell"]["years"])
        for _r, said in self._offered(led):
            again = self._led(said, **case)
            self.assertFalse({"carry_back_cant_tell", "carry_back_split_cant_tell"}
                             & set(again))

    @rule(R.CA_CARRY_04)
    def test_every_line_offered_is_one_the_ledger_takes(self):
        """The review's P2-1: 2023's lock records 60 carried back (no years), and
        2021 is rebuilt before it. A reading in which 2021 takes the claim 2023's
        lock needs leaves that lock's row short of its 60: no return had it, and
        its line was refused ("... but 2023's close-year lock records 60.00").
        Such a reading is dropped; what is offered is taken and settles it."""
        case = dict(nets=dict(y2019=60.0, y2020=60.0, y2021=-60.0, y2022=100.0,
                              y2023=-60.0, y2024=-60.0),
                    claims={2019: 60.0, 2020: 40.0, 2022: 40.0},
                    locks={2022: (0.0, 0.0, 0.0), 2023: (60.0, 0.0, 0.0)},
                    opening={2023: 0.0})
        led = self._led(**case)
        ct = led["carry_back_split_cant_tell"]
        self.assertEqual([(r["to"], r["carried_back"]) for r in ct["readings"]],
                         [({"2021": {"2019": 60.0}, "2023": {"2020": 40.0, "2022": 20.0}},
                           20.0),
                          ({"2021": {"2019": 60.0}, "2023": {"2020": 20.0, "2022": 40.0}},
                           0.0)])
        for r, said in self._offered(led):
            told = self._led(said, **case)
            self.assertNotIn("carry_back_split_cant_tell", told)
            self.assertEqual(self._figures(told), (r["carried_back"], r["carryforward"]))
            self.assertEqual(_row(told, 2023)["carried_back"], 60.0)
        # A lock after the year holds part of a claim: 2022's 40 can go to 2021
        # only as far as 2023's lock (20, with 2021 alone in its reach) leaves.
        held = dict(nets=dict(y2019=60.0, y2021=60.0, y2022=-80.0, y2023=-20.0,
                              y2024=-40.0), claims={2019: 60.0, 2021: 40.0},
                    locks={2021: (0.0, 0.0, 0.0), 2022: (40.0, 0.0, 40.0),
                           2023: (20.0, 0.0, 40.0)}, opening={2023: 40.0}, carried_in=40.0)
        led = self._led(**held)
        self.assertEqual([(r["to"], r["carried_back"], r["carryforward"])
                          for r in led["carry_back_split_cant_tell"]["readings"]],
                         [({"2022": {"2019": 40.0}}, 20.0, 60.0),
                          ({"2022": {"2019": 20.0, "2021": 20.0}}, 0.0, 80.0)])
        for r, said in self._offered(led):
            told = self._led(said, **held)
            self.assertNotIn("carry_back_split_cant_tell", told)
            self.assertEqual(_year_figures(told, 2024, 40.0)["carried_back"],
                             r["carried_back"])

    @rule(R.CA_CARRY_04)
    def test_the_project_year_s_entry_waits_for_the_earlier_years(self):
        """The review's P2-2: 40 and 60 claimed under 2020 and 2021; 2022's 40
        went to 2021, so 2024 carries 20 back to 2021. With 2024's own entry in
        force while 2022's readings were compared, both ends were cut to its
        total, 2022's entry was set aside, and the true entry was refused
        against a fold without it ("but 2024 carries back 40.00"; write 40:
        "but 2024 carries back 20.00")."""
        case = dict(claims={2020: 40.0, 2021: 60.0})
        alone = self._led({"2022:2021": 40.0}, **case)
        self.assertEqual((self._figures(alone), _row(alone, 2024)["carried_back_to"]),
                         ((20.0, 20.0), {"2021": 20.0}))
        both = self._led({"2022:2021": 40.0, "2024:2021": 20.0}, **case)
        self.assertEqual((self._figures(both), _row(both, 2022)["carried_back_to_said"],
                          _row(both, 2024)["carried_back_to_said"]),
                         ((20.0, 20.0), "entry", "entry"))
        for own in (40.0, 0.0):
            self.assertIn(f"says 2024 carries back {own:.2f}"
                          + (" to 2021" if own else "")
                          + ", but 2024 carries back 20.00",
                          self._refused({"2022:2021": 40.0, "2024:2021": own}, **case))
        # With the earlier year still open, the own entry answers nothing: asked.
        open_ = self._led({"2024:2021": 20.0}, **case)
        self.assertIn("carry_back_split_cant_tell", open_)
        self.assertNotIn("carried_back_to_said", _row(open_, 2024))

    @rule(R.CA_CARRY_04)
    def test_no_entry_is_set_aside_unfolded(self):
        """The guard: an entry for a year the ledger did not ask about is folded
        before it is called not needed; one that changes the project year's
        figures is taken. Shown with the question switched off. And past its
        bound on readings the ledger asks, never passes."""
        from unittest import mock
        from taxjson.bin.taxjson_carryover import _Readings
        with mock.patch.object(_Readings, "_orders", return_value=None):
            self.assertNotIn("carry_back_split_cant_tell", self._led())
            told = self._led({"2022:2021": 40.0})
            self.assertEqual((self._figures(told), _row(told, 2022)["carried_back_to_said"]),
                             ((0.0, 40.0), "entry"))
            none = self._led(
                {2022: 0.0}, nets=dict(y2018=60.0, y2020=-60.0, y2021=100.0,
                                       y2022=-80.0, y2024=-60.0),
                claims={2018: 40.0, 2021: 60.0})
            self.assertEqual((self._figures(none), _row(none, 2022)["carried_back_stated"]),
                             ((40.0, 20.0), 0.0))
        with mock.patch.object(_Readings, "MAX_READINGS", 1):
            ct = self._led()["carry_back_split_cant_tell"]
            self.assertEqual((ct["more"], len(ct["readings"])), (True, 1))
        # An entry that changes nothing is still a note, and changes nothing.
        import contextlib
        import io
        err = io.StringIO()
        far = dict(nets=dict(y2020=100.0, y2021=100.0, y2022=-40.0, y2025=-40.0),
                   locks={2022: (40.0, 0.0, 0.0)}, year=2025)
        with contextlib.redirect_stderr(err):
            noted = self._led({"2022:2021": 40.0}, **far)
        self.assertEqual(noted, self._led(**far))
        self.assertIn("entry for 2022 is no longer needed: 2022's close-year lock records "
                      "the same in all, and nothing of 2025 rests on which years it went "
                      "to", " ".join(err.getvalue().split()))

    @rule(R.CA_CARRY_04)
    def test_a_recorded_answer_stands_where_nothing_is_asked(self):
        """The review's P3 (b): 2024 closed on the answer that 2022 went to 2021.
        In 2025 nothing rests on 2022 any more, and an entry there that names
        2020 was noted as not needed; it contradicts the record, and is refused
        naming both, as where the ledger asks. So is a total that differs."""
        case = dict(nets=dict(self.NETS, y2025=-30.0), year=2025,
                    locks={2022: (40.0, 0.0, 0.0), 2024: (0.0, 0.0, 40.0)},
                    carried_in=40.0)
        rec = dict(case, lock_to=({2022: ({2021: 40.0}, 2024)}, {}))
        self.assertNotIn("carry_back_split_cant_tell", self._led(**rec))
        self.assertIn("[carryover] carried_back says 2022 carried back 40.00 to 2020, "
                      "but 2024's close-year lock records your answer 40.00 to 2021 for "
                      "it (carry_back_stated_to)", self._refused({"2022:2020": 40.0}, **rec))
        self._led({"2022:2021": 40.0}, **rec)                     # (the same: a note)
        gone = dict(nets=dict(y2021=100.0, y2022=-100.0, y2024=50.0, y2026=-30.0),
                    claims={2021: 40.0}, year=2026, locks={2024: (0.0, 50.0, 10.0)},
                    typed={2024: 60.0}, carried_in=10.0,
                    lock_stated={2022: (40.0, 2024)})
        self.assertIn("says 2022 carried back 10.00, but 2024's close-year lock records "
                      "your answer 40.00 for it (carry_back_stated)",
                      self._refused({2022: 10.0}, **gone))

    @rule(R.CA_CARRY_04)
    def test_a_later_project_s_warning_names_the_lock(self):
        """The review's P3 (a): 2024 closed on 60 typed in and the answer that
        2023 carried back 10, two figures the nets don't reconcile. 2025 reads
        both from 2024's lock: its warning says so, not "your [carryover]
        carried_back entries" and "[estimate] other_losses" it does not have."""
        from taxjson.bin.taxjson_carryover import render_brief
        from taxjson.lib.carry_back import not_in_nets_text
        nets = _nets(y2022=100.0, y2023=-100.0, y2024=50.0, y2025=-30.0)
        case = dict(year=2025, locks={2024: (30.0, 50.0, 0.0)}, typed={2024: 60.0},
                    carried_in=0.0)
        led = _asked(nets, {2022: 40.0}, lock_stated={2023: (10.0, 2024)}, **case)
        (h,) = led["carried_in_not_in_nets"]
        self.assertEqual((h["into"], h["carried_in_lock"], h["answered_in_lock"],
                          h["gain_not_in_nets"]), (2024, 2024, 2024, 30.0))
        self.assertEqual(
            not_in_nets_text(h),
            "the answers 2024's close-year lock recorded (carry_back_stated) leave "
            "90.00 carried into 2024 on these books' nets; 2024's lock opened with "
            "60.00 (its [estimate] other_losses), 30.00 less. Both hold only if the "
            "return of 2023 had 30.00 or more of capital gains that are not in these "
            "nets (T3 box 21, T5 box 18 not named in [[capital_gains_dividends]], an "
            "FX gain on foreign cash): check those answers and that balance against "
            "that return")
        self.assertIn("! 2024: carried in 60.00, 30.00 under what the answers in 2024's "
                      "lock leave on these nets", render_brief(led, "CAD", None))
        mine = _asked(nets, {2022: 40.0}, stated={2023: 10.0}, year=2024,
                      carried_in=60.0, typed_in=True)
        self.assertIn("your [carryover] carried_back entries leave 90.00 carried into "
                      "2024 on these books' nets; you carried in 60.00 ([estimate] "
                      "other_losses), 30.00 less", not_in_nets_text(
                          mine["carried_in_not_in_nets"][0]))

    def test_the_other_order(self):
        """`latest_first_before`: the rows before that year meet the latest
        claimed year first; the year itself and those after it, the oldest.
        `back_to`: a row with its split on record meets those years and no
        other, whichever the order."""
        nets = _nets(y2020=100.0, y2021=100.0, y2022=-40.0, y2023=-30.0)
        claims = {2020: 40.0, 2021: 40.0}
        first = build_canada_ledger(nets, claims)
        other = build_canada_ledger(nets, claims, latest_first_before=2023)
        self.assertEqual((_row(first, 2022)["carried_back_to"],
                          _row(first, 2023)["carried_back_to"]),
                         ({"2020": 40.0}, {"2021": 30.0}))
        self.assertEqual((_row(other, 2022)["carried_back_to"],
                          _row(other, 2022)["carried_back_split"],
                          _row(other, 2023)["carried_back_to"]),
                         ({"2021": 40.0}, "latest_first", {"2020": 30.0}))
        both = build_canada_ledger(nets, claims, latest_first_before=2024)
        self.assertEqual(_row(both, 2023)["carried_back_to"], {"2020": 30.0})
        # ...and a lock's short-fall (a carry-back the books don't hold) too.
        short = build_canada_ledger(_nets(y2020=100.0, y2021=100.0, y2022=0.0),
                                    claims, {2022: 0.0}, {2022: 40.0}, {2022: 0.0},
                                    latest_first_before=2023)
        self.assertEqual(_row(short, 2022)["carried_back_to"], {"2021": 40.0})
        for order in (None, 2024):
            said = build_canada_ledger(nets, claims, stated={2022: 40.0},
                                       back_to={2022: {2020: 10.0, 2021: 30.0}},
                                       latest_first_before=order)
            self.assertEqual((_row(said, 2022)["carried_back_to"],
                              "carried_back_split" in _row(said, 2022),
                              sorted(_row(said, 2023)["carried_back_to"].items())),
                             ({"2020": 10.0, "2021": 30.0}, False,
                              [("2020", 30.0)] if order is None else
                              [("2020", 20.0), ("2021", 10.0)]), order)
        # A lock's carry-back the books don't hold goes to the years on record.
        short = build_canada_ledger(_nets(y2020=100.0, y2021=100.0, y2022=0.0),
                                    claims, {2022: 0.0}, {2022: 40.0}, {2022: 0.0},
                                    back_to={2022: {2021: 40.0}})
        self.assertEqual((_row(short, 2022)["carried_back_to"],
                          short.get("claims_waiting")), ({"2021": 40.0}, {"2020": 40.0}))

    @rule(R.CA_CARRY_04)
    def test_a_recorded_split_is_read_and_an_entry_that_differs_is_refused(self):
        """2023 closed on the answer that 2022 went to 2021 (`carry_back_stated_to`
        in its lock): 2024's project has no entry and is not asked. An entry
        there that says otherwise is refused, naming both; a total that agrees
        leaves the recorded years standing."""
        chain = dict(locks={2023: (0.0, 0.0, 0.0)}, typed={2023: 0.0})
        self.assertIn("carry_back_split_cant_tell", self._led(**chain))
        rec = dict(chain, lock_to=({2022: ({2021: 40.0}, 2023)}, {}))
        led = self._led(**rec)
        self.assertNotIn("carry_back_split_cant_tell", led)
        row = _row(led, 2022)
        self.assertEqual((self._figures(led), row["carried_back_to"],
                          row["carried_back_to_said"], row["carried_back_to_in_lock"],
                          row["carried_back_stated_in_lock"]),
                         ((0.0, 40.0), {"2021": 40.0}, "lock", 2023, 2023))
        from taxjson.bin.taxjson_carryover import render_brief
        self.assertIn("2022: carried back 40.00 (to 2021 40.00), your figure "
                      "([carryover] carried_back, as 2023's close-year lock recorded "
                      "it), not these books'",
                      " ".join(render_brief(led, "CAD", None).split()))
        self.assertIn(
            "[carryover] carried_back says 2022 carried back 40.00 to 2020, but 2023's "
            "close-year lock records your answer 40.00 to 2021 for it "
            "(carry_back_stated_to): correct that entry in taxjson.toml, or close 2023 "
            "again on the right one", self._refused({"2022:2020": 40.0}, **rec))
        self.assertIn("says 2022 carried back 10.00, but 2023's close-year lock records "
                      "your answer 40.00 for it (carry_back_stated)",
                      self._refused({2022: 10.0}, **rec))
        same = self._led({2022: 40.0}, **rec)
        self.assertEqual((self._figures(same), _row(same, 2022)["carried_back_to"]),
                         ((0.0, 40.0), {"2021": 40.0}))
        # A recorded total with no years, and an entry that gives them.
        total = dict(chain, lock_stated={2022: (40.0, 2023)})
        self.assertIn("carry_back_split_cant_tell", self._led(**total))
        self.assertEqual(self._figures(self._led({"2022:2021": 40.0}, **total)),
                         (0.0, 40.0))
        self.assertIn("says 2022 carried back 30.00, but 2023's close-year lock records "
                      "your answer 40.00 for it (carry_back_stated)",
                      self._refused({"2022:2021": 30.0}, **total))

    def test_the_lock_s_splits_are_read_whole(self):
        from taxjson.bin.taxjson_carryover import (LockUnreadable, _recorded_splits,
                                                   lock_figure)
        from _tmpfiles import private_dir
        ncl = {"opening": 0.0, "opening_source": "none", "created": 40.0,
               "applied": 0.0, "carried_back": 40.0, "closing": 0.0}
        path = Path(private_dir()) / "2024.json"

        def lock(**more):
            path.write_text(json.dumps({
                "totals": {"realized": -40.0, "dispositions": 1},
                "carryforwards": {"net_capital_loss": dict(ncl, **more)}}))
            return lock_figure(path, "canada")
        info = lock(carried_back_to={"2021": 30.0, "2022": 10.0},
                    carry_back_stated_to={"2022": {"2021": 40.0}})
        self.assertEqual((info["back_to"], info["stated_to"]),
                         ({2021: 30.0, 2022: 10.0}, {2022: {2021: 40.0}}))
        self.assertEqual(_recorded_splits({2024: info}, 2025),
                         ({2022: ({2021: 40.0}, 2024)}, {2024: {2021: 30.0, 2022: 10.0}}))
        self.assertEqual(_recorded_splits({2024: info}, 2024), ({}, {}))
        before = lock()
        self.assertFalse({"back_to", "stated_to"} & set(before))    # (read as before)
        for bad in (dict(carried_back_to="x"), dict(carried_back_to={"2021": "x"}),
                    dict(carried_back_to={"2021": 30.0}),            # (not the 40)
                    dict(carried_back_to={"2020": 40.0}),            # (4 years back)
                    dict(carried_back_to={"2021": 50.0, "2022": -10.0}),
                    dict(carry_back_stated_to=[]),
                    dict(carry_back_stated_to={"2022": 40.0}),
                    dict(carry_back_stated_to={"2022": {"2018": 40.0}})):
            with self.assertRaises(LockUnreadable, msg=bad):
                lock(**bad)

    def test_the_lock_records_years_that_foot_to_its_carry_back(self):
        """`lock_split`: the years are cut to the lock's carry-back (the latest
        gives back first) and footed to it (the oldest takes the cents between
        the ledger's rounded parts and its rounded total); years that are no
        amounts, or that add up to another figure, stop the close; with no
        years given the lock holds the total alone, as one written before."""
        from taxjson.lib.carry_back import by_year_said, lock_split, split_proposed_said

        def lock(back, to, how=None, stated_to=None):
            ncl = {"carried_back": back}
            return lock_split(ncl, back, to, how, stated_to), ncl
        self.assertEqual(lock(60.0, {"2021": 40.0, "2022": 20.0}, "oldest_first"),
                         ("", {"carried_back": 60.0, "carried_back_split": "oldest_first",
                               "carried_back_to": {"2021": 40.0, "2022": 20.0}}))
        self.assertEqual(lock(50.0, {"2021": 40.0, "2022": 20.0})[1]["carried_back_to"],
                         {"2021": 40.0, "2022": 10.0})
        self.assertEqual(lock(33.33, {"2021": 16.67, "2022": 16.67})[1]["carried_back_to"],
                         {"2021": 16.67, "2022": 16.66})
        self.assertEqual(lock(33.34, {"2021": 16.66, "2022": 16.67})[1]["carried_back_to"],
                         {"2021": 16.67, "2022": 16.67})
        self.assertEqual(lock(40.0, None), ("", {"carried_back": 40.0}))
        self.assertEqual(lock(0.0, None, None, {"2022": {"2021": 40.0}}),
                         ("", {"carried_back": 0.0,
                               "carry_back_stated_to": {"2022": {"2021": 40.0}}}))
        for bad in (dict(to={"2021": "x"}), dict(to={"x": 1.0}), dict(to={}),
                    dict(to={"2021": 40.0}, how="x"), dict(to={"2021": 30.0}),
                    dict(to={"2021": 50.0}, how="entry"),
                    dict(to={"2021": 40.0}, stated_to={"2022": 40.0}),
                    dict(to={"2021": 40.0}, stated_to="x")):
            why, ncl = lock(40.0, bad.get("to"), bad.get("how"), bad.get("stated_to"))
            self.assertTrue(why, bad)
            self.assertNotIn("carried_back_to", ncl)
        both = {"carried_back_to": {"2021": 40.0, "2022": 20.0},
                "carried_back_split": "oldest_first"}
        self.assertEqual(by_year_said(both), "; carry-back by year: 2021 40.00, 2022 20.00")
        self.assertIn("oldest [carryover] claimed year first: 2021 40.00, 2022 20.00",
                      split_proposed_said(both, 2024))
        one = {"carried_back_to": {"2021": 40.0}}
        self.assertEqual((by_year_said(one), split_proposed_said(one, 2024),
                          by_year_said({}), split_proposed_said({}, 2024)),
                         ("", "", "", ""))

    def test_an_entry_that_is_not_whole_is_no_entry(self):
        """What reads the ledger takes `carry_back_split_cant_tell` whole or
        refuses it: a damaged one is never "the ledger can tell"."""
        from taxjson.lib.carry_back import (cant_tell_act, cant_tell_text, entry_text,
                                            not_in_nets, reading_when, split_cant_tell,
                                            split_text, trim_to, went_to)
        whole = {"years": [2022], "unchecked": [2022], "answered": {"2021": 5.0},
                 "readings": [self.OLDEST, self.LATEST]}
        self.assertIsNone(split_cant_tell({"rows": []}))
        self.assertEqual(split_cant_tell({"carry_back_split_cant_tell": whole}), whole)
        self.assertIn("`carried_back = { 2021 = 5.00, 2022 = { 2020 = 40.00 } }` if "
                      "2022 carried back 40.00 to 2020 (", cant_tell_text(
                          {"carry_back_split_cant_tell": whole}))
        self.assertIn("(there are more readings than these)", cant_tell_text(
            {"carry_back_split_cant_tell": dict(whole, more=True)}))
        self.assertEqual(reading_when(whole, {"to": {"2021": {}, "2022": {
            "2019": 10.0, "2020": 30.0}}}), "2021 carried nothing back and 2022 "
                         "carried back 40.00 to 2019 (10.00) and 2020 (30.00)")
        self.assertIn("2022", cant_tell_act({"carry_back_split_cant_tell": whole}))
        for bad in ({}, "x", dict(whole, years=[]), dict(whole, readings=[]),
                    dict(whole, readings=[self.OLDEST, dict(self.LATEST, to="x")]),
                    dict(whole, readings=[self.OLDEST, dict(self.LATEST, to={})]),
                    dict(whole, readings=[self.OLDEST,
                                          dict(self.LATEST, carried_back="0")]),
                    dict(whole, answered={"2021": "x"})):
            with self.assertRaises(ValueError, msg=bad):
                split_cant_tell({"carry_back_split_cant_tell": bad})
            with self.assertRaises(ValueError, msg=bad):
                cant_tell_text({"carry_back_split_cant_tell": bad})
        self.assertEqual((entry_text(0.0), entry_text(40.0), entry_text({}),
                          entry_text({"2021": 40.0, "2020": 0.0})),
                         ("0.00", "40.00", "0", "{ 2021 = 40.00 }"))
        self.assertEqual((split_text({"2021": 20.0, "2020": 40.0}), split_text({}),
                          went_to({"2021": 40.0}), went_to({"2021": 20.0, 2020: 40.0}),
                          went_to(None)),
                         ("2020 40.00, 2021 20.00", "", " to 2021",
                          " to 2020 (40.00) and 2021 (20.00)", ""))
        self.assertEqual((trim_to({"2020": 40.0, "2021": 20.0}, 45.0),
                          trim_to({"2020": 40.0, "2021": 20.0}, 60.0),
                          trim_to({"2020": 40.0, "2021": 20.0}, 30.0), trim_to(None, 5.0)),
                         ({"2020": 40.0, "2021": 5.0}, {"2020": 40.0, "2021": 20.0},
                          {"2020": 30.0}, {}))
        entry = {"into": 2024, "carried_in": 60.0, "leaves": 90.0, "leaves_least": 90.0,
                 "years": [2023], "gain_not_in_nets": 30.0}
        self.assertEqual(not_in_nets({"carried_in_not_in_nets": [entry]}), [entry])
        self.assertEqual(not_in_nets({}), [])
        for bad in ("x", [dict(entry, years=[])], [dict(entry, loss_not_in_nets=1.0)],
                    [{k: v for k, v in entry.items() if k != "gain_not_in_nets"}]):
            with self.assertRaises(ValueError, msg=bad):
                not_in_nets({"carried_in_not_in_nets": bad})

    @rule(R.CA_CARRY_05)
    def test_handoff_says_whose_split_it_compared(self):
        """The claim under the record year is compared less the part of this
        project's loss carried back to it: where that part is the ledger's
        proposal (another split open), handoff says so; and where the record's
        own carry-back by year was one, that this project reads it."""
        from taxjson.lib.carryforward import LedgerCarryBack, handoff_issues
        from taxjson.project.settings import Settings
        cfg = {"settings": Settings.from_table({"country": "canada", "year": 2024}),
               "carryover": {"claimed": {"2022": 40.0, "2023": 40.0}}}
        ncl = {"applied": 20.0, "closing": 0.0}
        record = {"year": 2023, "carryforwards": {"net_capital_loss": ncl}}
        plain = handoff_issues(Path("."), cfg, record,
                               LedgerCarryBack(amounts={2022: 40.0, 2023: 20.0}))
        self.assertEqual((plain.issues, plain.order_notes), ([], ()))
        res = handoff_issues(Path("."), cfg, record, LedgerCarryBack(
            amounts={2022: 40.0, 2023: 20.0}, proposed="2022 40.00, 2023 20.00"))
        self.assertEqual(res.issues, [])
        self.assertEqual(res.order_notes, (
            "[carryover] claimed 2023 was compared taking 20.00 of this project's "
            "loss as carried back to 2023: `tjs carryover`'s proposal for this year's "
            "T1A, the oldest [carryover] claimed year first (2022 40.00, 2023 20.00). "
            "If the T1A names other years, say so: `carried_back = { 2024 = { YEAR = "
            "AMOUNT } }` under [carryover].",))
        ncl.update(carried_back=30.0, carried_back_to={"2021": 20.0, "2022": 10.0},
                   carried_back_split="oldest_first")
        res = handoff_issues(Path("."), cfg, record,
                             LedgerCarryBack(amounts={2023: 20.0}))
        self.assertEqual(res.order_notes, (
            "The 2023 record split its T1A carry-back between the [carryover] claimed "
            "years by `tjs carryover`'s proposal, the oldest first (2021 20.00, 2022 "
            "10.00): no "
            "[carryover] carried_back entry gave the years. This project's ledger "
            "reads that split. If 2023's T1A named other years, say so in the 2023 "
            "project (`carried_back = { 2023 = { YEAR = AMOUNT } }`) and re-close it "
            "with --force.",))
        del ncl["carried_back_split"]                 # (the person gave the years)
        self.assertEqual(handoff_issues(Path("."), cfg, record, LedgerCarryBack(
            amounts={2023: 20.0})).order_notes, ())

    def _says(self, d):
        """(the ledger's entry or None; the three commands agree it can't tell)."""
        led = json.loads(_ok(_cli(d, "carryover", "--json")).stdout)
        asked = led.get("carry_back_split_cant_tell")
        self.assertNotIn("carry_back_cant_tell", led)
        for flags in ((), ("--details",)):
            self.assertEqual("can't tell" in _ok(_cli(d, "carryover", *flags)).stdout,
                             bool(asked), flags)
        res = _ok(_cli(d, "estimate", "--json"))
        est = json.loads(res.stdout)["estimate"]
        self.assertEqual("carry_back_unknown" in est, bool(asked))
        if asked:
            self.assertNotIn("losses_carried_forward", est)
            self.assertIn("carry-back of this year's loss unknown (`tjs carryover`): "
                          "these books can't tell which [carryover] claimed years",
                          " ".join(res.stderr.split()))
            self.assertIn("the carry-forward is unknown", _ok(_cli(d, "estimate")).stdout)
        return asked, est

    def _answer(self, d, line):
        toml = d / "taxjson.toml"
        text = "\n".join(x for x in toml.read_text().splitlines()
                         if not x.startswith("carried_back"))
        toml.write_text(text.replace("claimed =", line + "claimed =") + "\n")

    def _ncl(self, d):
        return json.loads((d / "filed" / f"{d.name}.json").read_text())[
            "carryforwards"]["net_capital_loss"]

    @rule(R.CA_CARRY_04)
    def test_end_to_end_the_worked_example(self):
        """No lock, no entry: `tjs carryover` can't tell, the estimate's
        carry-forward is unknown and `tjs close-year` stops, giving the line for
        each reading. A total leaves the stop; the by-target line closes the
        year, and the lock records the years."""
        sales = {2020: ("ZZQA", 600.0), 2021: ("ZZQB", 600.0), 2022: ("ZZQC", 460.0),
                 2024: ("ZZQD", 460.0)}
        with tempfile.TemporaryDirectory() as td:
            d = TestAGainYearWithALossLeft._project(
                self, Path(td), False, None, sales, "2020 = 40.00, 2021 = 40.00",
                first=None)
            asked, _est = self._says(d)
            self.assertEqual(asked["readings"], [self.OLDEST, self.LATEST])
            closed = _cli(d, "close-year", "--yes")
            self.assertEqual(closed.returncode, 1, closed.stdout + closed.stderr)
            said = " ".join((closed.stdout + closed.stderr).split())
            self.assertIn("could not compute the carry-forwards the lock records", said)
            self.assertIn("add `carried_back = { 2022 = { 2020 = 40.00 } }` if 2022 "
                          "carried back 40.00 to 2020 (this year then carries back "
                          "40.00 to 2021 and 0.00 forward); or `carried_back = { 2022 = "
                          "{ 2021 = 40.00 } }` if 2022 carried back 40.00 to 2021 "
                          "(this year then carries back 0.00 and 40.00 forward)", said)
            self.assertFalse((d / "filed" / "2024.json").exists())
            self._answer(d, "carried_back = { 2022 = 40.00 }\n")
            asked, _est = self._says(d)
            self.assertEqual((asked["unchecked"], len(asked["readings"])), ([], 2))
            self.assertEqual(_cli(d, "close-year", "--yes").returncode, 1)
            self._answer(d, "carried_back = { 2022 = { 2018 = 40.00 } }\n")
            bad = _cli(d, "carryover")
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("[carryover] carried_back: 2022 = { 2018 = 40.0 }: 2018 is not "
                          "one of the 3 years before 2022", " ".join(bad.stderr.split()))
            self._answer(d, "carried_back = { 2022 = { 2021 = 40.00 } }\n")
            asked, est = self._says(d)
            self.assertIsNone(asked)
            self.assertEqual((est["losses_carried_back"], est["losses_carried_forward"],
                              est["carry_back_stated"], est["carry_back_stated_to"]),
                             (0.0, 40.0, {"2022": 40.0}, {"2022": {"2021": 40.0}}))
            self.assertNotIn("losses_carried_back_to", est)
            _ok(_cli(d, "close-year", "--yes"))
            ncl = self._ncl(d)
            self.assertEqual((ncl.get("carried_back", 0.0), ncl["closing"],
                              ncl["carry_back_stated"], ncl["carry_back_stated_to"],
                              "carried_back_to" in ncl),
                             (0.0, 40.0, {"2022": 40.0}, {"2022": {"2021": 40.0}}, False))
        with tempfile.TemporaryDirectory() as td:
            d = TestAGainYearWithALossLeft._project(
                self, Path(td), False, None, sales, "2020 = 40.00, 2021 = 40.00",
                first=None, extra="carried_back = { 2022 = { 2020 = 40.00 } }\n")
            asked, est = self._says(d)
            self.assertIsNone(asked)
            self.assertEqual((est["losses_carried_back"], est["losses_carried_forward"],
                              est["losses_carried_back_to"]), (40.0, 0.0, {"2021": 40.0}))
            self.assertNotIn("carry_back_split", est)       # (one year in reach)
            _ok(_cli(d, "close-year", "--yes"))
            ncl = self._ncl(d)
            self.assertEqual((ncl["carried_back"], ncl["closing"], ncl["carried_back_to"],
                              ncl["carry_back_stated_to"], "carried_back_split" in ncl),
                             (40.0, 0.0, {"2021": 40.0}, {"2022": {"2020": 40.0}}, False))
            _ok(_cli(d, "check-filed"))

    def _chain(self, top, answer=""):
        """Year folders 2024 and 2025 on one inputs folder: gains of 100 in 2021
        and 2022, losses of 40 in 2023, 20 in 2024 and 40 in 2025, 40 claimed
        under each of 2021 and 2022; 2025 names 2024's lock."""
        (top / "inputs" / "margin").mkdir(parents=True)
        sales = {2021: ("ZZQA", 600.0), 2022: ("ZZQB", 600.0), 2023: ("ZZQC", 460.0),
                 2024: ("ZZQD", 480.0), 2025: ("ZZQE", 460.0)}
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
            (d / "taxjson.toml").write_text(
                toml + "\n[carryover]\n" + (answer if year == 2024 else "")
                + "claimed = { 2021 = 40.00, 2022 = 40.00 }\n")
            dirs.append(d)
        return dirs

    @rule(R.CA_CARRY_04)
    def test_end_to_end_it_is_asked_once_along_a_chain_of_years(self):
        """2024 carries 20 back whichever claim 2023 met, to the other year: it
        is asked which (the years are what its lock records), answered, and
        closed. 2025's carry-back depends on it too (20 if 2023 went to 2021,
        nothing if to 2022): its project reads the answer in 2024's lock, with
        2024's own years, and is not asked. An entry there that says otherwise
        is refused, naming both."""
        with tempfile.TemporaryDirectory() as td:
            d24, d25 = self._chain(Path(td))
            _ok(_cli(d24, "run", "--no-input"))
            asked, _est = self._says(d24)
            self.assertEqual(
                [(r["to"]["2023"], r["carried_back"], r["carried_back_to"])
                 for r in asked["readings"]],
                [({"2021": 40.0}, 20.0, {"2022": 20.0}),
                 ({"2022": 40.0}, 20.0, {"2021": 20.0})])
            self.assertEqual(_cli(d24, "close-year", "--yes").returncode, 1)
            self._answer(d24, "carried_back = { 2023 = { 2021 = 40.00 } }\n")
            asked, est = self._says(d24)
            self.assertIsNone(asked)
            self.assertEqual((est["losses_carried_back"], est["losses_carried_back_to"]),
                             (20.0, {"2022": 20.0}))
            _ok(_cli(d24, "close-year", "--yes"))
            ncl = self._ncl(d24)
            self.assertEqual((ncl["carried_back"], ncl["closing"], ncl["carried_back_to"],
                              ncl["carry_back_stated_to"]),
                             (20.0, 0.0, {"2022": 20.0}, {"2023": {"2021": 40.0}}))
            _ok(_cli(d25, "run", "--no-input"))
            asked, est = self._says(d25)
            self.assertIsNone(asked)
            self.assertEqual((est["losses_carried_back"], est["losses_carried_forward"],
                              est["losses_carried_back_to"], est["carry_back_stated_to"]),
                             (20.0, 20.0, {"2022": 20.0}, {"2023": {"2021": 40.0}}))
            led = json.loads(_ok(_cli(d25, "carryover", "--json")).stdout)
            self.assertEqual(
                [(_row(led, y)["carried_back_to"], _row(led, y)["carried_back_to_said"],
                  _row(led, y)["carried_back_to_in_lock"]) for y in (2023, 2024)],
                [({"2021": 40.0}, "lock", 2024), ({"2022": 20.0}, "lock", 2024)])
            self._answer(d25, "carried_back = { 2023 = { 2022 = 40.00 } }\n")
            other = _cli(d25, "carryover")
            self.assertEqual(other.returncode, 1, other.stdout + other.stderr)
            self.assertIn("[carryover] carried_back says 2023 carried back 40.00 to "
                          "2022, but 2024's close-year lock records your answer 40.00 "
                          "to 2021 for it (carry_back_stated_to)",
                          " ".join(other.stderr.split()))
            self._answer(d25, "")
            _ok(_cli(d25, "handoff"))
            _ok(_cli(d25, "close-year", "--yes"))
            ncl = self._ncl(d25)
            self.assertEqual((ncl["opening"], ncl["carried_back"], ncl["closing"],
                              ncl["carried_back_to"], ncl["carry_back_stated_to"]),
                             (0.0, 20.0, 20.0, {"2022": 20.0}, {"2023": {"2021": 40.0}}))

    @rule(R.CA_CARRY_04)
    def test_end_to_end_the_proposal_is_said_and_locked_and_handoff_repeats_it(self):
        """2024 loses 60 with 40 claimed under each of 2022 and 2023 and no
        earlier loss: nothing to ask, and the years are the ledger's proposal.
        The estimate and `tjs close-year` say so, the lock records the years as
        a proposal, and 2025's `tjs handoff` says its ledger reads them. With
        the project year's own entry, the years are the person's."""
        sales = {2022: ("ZZQA", 600.0), 2023: ("ZZQB", 600.0), 2024: ("ZZQC", 440.0),
                 2025: ("ZZQD", 500.0)}
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            (top / "inputs" / "margin").mkdir(parents=True)
            (top / "inputs" / "margin" / "t.tt").write_text(
                "".join(_trades(yr, *sale) for yr, sale in sorted(sales.items())))
            for year in (2024, 2025):
                d = top / str(year)
                d.mkdir()
                toml = _TOML.replace("year = 2023", f"year = {year}")
                if year == 2025:
                    toml = toml.replace(
                        "[accounts.margin]",
                        'prior_year_record = "../2024/filed/2024.json"\n\n'
                        '[accounts.margin]')
                (d / "taxjson.toml").write_text(
                    toml + "\n[carryover]\nclaimed = { 2022 = 40.00, 2023 = 40.00 }\n")
            d24, d25 = top / "2024", top / "2025"
            _ok(_cli(d24, "run", "--no-input"))
            asked, est = self._says(d24)
            self.assertIsNone(asked)
            self.assertEqual((est["losses_carried_back"], est["losses_carried_back_to"],
                              est["carry_back_split"]),
                             (60.0, {"2022": 40.0, "2023": 20.0}, "oldest_first"))
            self.assertIn("T1A carry-back by year: 2022 40.00, 2023 20.00 (proposed, the "
                          "oldest claimed year first: `tjs carryover --details`)",
                          _ok(_cli(d24, "estimate")).stdout)
            self.assertIn("! 2024: T1A carry-back by year, oldest first: 2022 40.00, "
                          "2023 20.00 (a proposal)", _ok(_cli(d24, "carryover")).stdout)
            closed = _ok(_cli(d24, "close-year", "--yes", "--details"))
            said = " ".join((closed.stdout + closed.stderr).split())
            self.assertIn("carry-back by year: 2022 40.00, 2023 20.00", said)
            self.assertIn("the T1A carry-back by year is `tjs carryover`'s proposal, the "
                          "oldest [carryover] claimed year first: 2022 40.00, 2023 "
                          "20.00. The lock records it (carried_back_to)", said)
            ncl = self._ncl(d24)
            self.assertEqual((ncl["carried_back"], ncl["carried_back_to"],
                              ncl["carried_back_split"]),
                             (60.0, {"2022": 40.0, "2023": 20.0}, "oldest_first"))
            _ok(_cli(d25, "run", "--no-input"))
            hand = _cli(d25, "handoff", "--details")
            self.assertIn("The 2024 record split its T1A carry-back between the "
                          "[carryover] claimed years by `tjs carryover`'s proposal, the "
                          "oldest first (2022 40.00, 2023 20.00)",
                          " ".join((hand.stdout + hand.stderr).split()))
            # The person's own years for 2024: the same 60, the other way round.
            self._answer(d24, "carried_back = { 2024 = { 2022 = 20.00, 2023 = 40.00 } }\n")
            est = json.loads(_ok(_cli(d24, "estimate", "--json")).stdout)["estimate"]
            self.assertEqual((est["losses_carried_back_to"], est["carry_back_split"]),
                             ({"2022": 20.0, "2023": 40.0}, "entry"))
            _ok(_cli(d24, "close-year", "--yes", "--force"))
            ncl = self._ncl(d24)
            self.assertEqual((ncl["carried_back_to"], "carried_back_split" in ncl),
                             ({"2022": 20.0, "2023": 40.0}, False))
            self._answer(d24, "carried_back = { 2024 = { 2022 = 20.00, 2023 = 30.00 } }\n")
            short = _cli(d24, "carryover")
            self.assertEqual(short.returncode, 1, short.stdout + short.stderr)
            self.assertIn("says 2024 carries back 50.00 to 2022 (20.00) and 2023 "
                          "(30.00), but 2024 carries back 60.00",
                          " ".join(short.stderr.split()))


class TestTheAnswerALockRecorded(unittest.TestCase):
    """TJ-4886: a question answered at one close is not asked again at the next.
    The lock records the answer (`carry_back_stated`), and a later project reads
    it where it has no entry of its own."""
    NETS = dict(y2022=100.0, y2023=-100.0, y2024=50.0, y2025=-30.0)
    CASE = dict(year=2025, locks={2024: (0.0, 50.0, 10.0)}, typed={2024: 60.0},
                carried_in=10.0)

    @rule(R.CA_CARRY_04)
    def test_it_answers_where_the_project_says_nothing(self):
        from taxjson.bin.taxjson_carryover import render_brief
        nets = _nets(**self.NETS)
        self.assertIn("carry_back_cant_tell", _asked(nets, {2022: 40.0}, **self.CASE))
        led = _asked(nets, {2022: 40.0}, lock_stated={2023: (40.0, 2024)}, **self.CASE)
        self.assertNotIn("carry_back_cant_tell", led)
        self.assertEqual((_row(led, 2023)["carried_back_stated"],
                          _row(led, 2023)["carried_back_stated_in_lock"],
                          _row(led, 2025)["carried_back"]), (40.0, 2024, 0.0))
        self.assertIn("2023: carried back 40.00, your figure ([carryover] carried_back, "
                      "as 2024's close-year lock recorded it), not these books'",
                      " ".join(render_brief(led, "CAD", None).split()))
        # The same entry in this project too: one figure, shown as the entry.
        same = _asked(nets, {2022: 40.0}, lock_stated={2023: (40.0, 2024)},
                      stated={2023: 40.0}, **self.CASE)
        self.assertNotIn("carried_back_stated_in_lock", _row(same, 2023))
        # An answer for a year nothing rests on is not read at all.
        gain = _asked(_nets(**dict(self.NETS, y2025=30.0)), {2022: 40.0},
                      lock_stated={2023: (0.0, 2024)}, **self.CASE)
        self.assertNotIn("carried_back_stated", _row(gain, 2023))

    @rule(R.CA_CARRY_04)
    def test_an_entry_that_says_otherwise_is_refused(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stop:
            _asked(_nets(**self.NETS), {2022: 40.0}, lock_stated={2023: (40.0, 2024)},
                   stated={2023: 10.0}, **self.CASE)
        self.assertEqual(stop.exception.code, 1)
        self.assertIn("[carryover] carried_back says 2023 carried back 10.00, but 2024's "
                      "close-year lock records your answer 40.00 for it "
                      "(carry_back_stated): correct that entry in taxjson.toml, or close "
                      "2024 again on the right one", " ".join(err.getvalue().split()))

    def test_the_lock_s_table_is_read_whole(self):
        from taxjson.bin.taxjson_carryover import LockUnreadable, lock_figure
        from _tmpfiles import private_dir
        ncl = {"opening": 60.0, "opening_source": "[estimate] other_losses",
               "created": 0.0, "applied": 50.0, "closing": 10.0}
        path = Path(private_dir()) / "2024.json"
        for said, want in (({"2023": 40.0}, {2023: 40.0}), ({}, {}), ("x", None),
                           ({"2023": "x"}, None), ({"y": 1.0}, None)):
            path.write_text(json.dumps({
                "totals": {"realized": 50.0, "dispositions": 1},
                "carryforwards": {"net_capital_loss": dict(ncl, carry_back_stated=said)}}))
            if want is None:
                with self.assertRaises(LockUnreadable, msg=said):
                    lock_figure(path, "canada")
            else:
                self.assertEqual(lock_figure(path, "canada")["stated"], want)
        path.write_text(json.dumps({"totals": {"realized": 50.0, "dispositions": 1},
                                    "carryforwards": {"net_capital_loss": ncl}}))
        self.assertNotIn("stated", lock_figure(path, "canada"))


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
