"""TJ-1495: a closed year's row in `tjs carryover`, with a carried-in balance and
a T1A claim under an earlier year, shows what its lock holds. By hand: 50 carried
in, a gain of 20 applied, 30 left, a claim of 40: back 30, closing 0.

Synthetic data only: made-up tickers, amounts under a thousand.

Formerly: test_fix_tj1495_row
"""
import itertools
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R
from _support.years_carryover_ledger import _cli
from _support.years_carryover_folders import _nets, _row, _trades, _ok, _build_years, _SALES

from taxjson.bin.taxjson_carryover import build_canada_ledger


class _Ledger(unittest.TestCase):
    """A 2021 gain, a 2022 loss of 50 closed, and 2023 closed."""
    def ledger(self, y2023=20.0, claimed=None, applied=20.0, back=30.0, closing=0.0,
               carried_in=50.0, **kw):
        return build_canada_ledger(
            _nets(y2021=100.0, y2022=-carried_in, y2023=y2023),
            {2021: 40.0} if claimed is None else claimed,
            carried={2022: carried_in, 2023: closing},
            locked_back={2022: 0.0, 2023: back},
            locked_applied={2022: 0.0, 2023: applied}, own_year=2023, **kw)


class TestTheOwnYearsRow(_Ledger):
    @rule(R.CA_CARRY_04)
    def test_it_shows_the_lock_s_figures(self):
        led = self.ledger()
        r = _row(led, 2023)
        self.assertEqual((r["carried_back"], r["claimed_applied"],
                          r["carryforward_balance"]), (30.0, 50.0, 0.0))
        self.assertEqual(r["carried_back_to"], {"2021": 30.0})
        self.assertEqual(r["rebuilt_balance"], 0.0)
        self.assertNotIn("applied_over_balance", r)
        self.assertEqual(led["unmatched_claims"], 10.0)

    @rule(R.CA_CARRY_04)
    def test_a_return_that_applied_it_all_leaves_no_carry_back(self):
        r = _row(self.ledger(y2023=100.0, applied=50.0, back=0.0), 2023)
        self.assertEqual((r["carried_back"], r["carryforward_balance"]), (0.0, 0.0))

    @rule(R.CA_CARRY_04)
    def test_a_claim_the_balance_covers_is_untouched(self):
        r = _row(self.ledger(claimed={2021: 20.0}, back=20.0, closing=10.0), 2023)
        self.assertEqual((r["carried_back"], r["claimed_applied"],
                          r["carryforward_balance"]), (20.0, 40.0, 10.0))

    @rule(R.CA_CARRY_04)
    def test_the_year_s_own_entry_is_met_by_what_the_lock_applied(self):
        # The fold uses what the return did; the row's APPLIED is the entry's,
        # which wins over the lock's (CA-CARRY-04).
        for entry, shown in ((20.0, 50.0), (5.0, 35.0), (0.0, 30.0)):
            with self.subTest(entry=entry):
                r = _row(self.ledger(claimed={2021: 40.0, 2023: entry}), 2023)
                self.assertEqual((r["carried_back"], r["claimed_applied"],
                                  r["carryforward_balance"], r["rebuilt_balance"]),
                                 (30.0, shown, 0.0, 0.0))

    @rule(R.CA_CARRY_04)
    def test_the_offer_to_apply_is_the_gain_less_the_entry_or_the_lock(self):
        # Net 45, 30 carried: an entry wins over the lock's 20 here too.
        for claimed, offered in (({}, 25.0), ({2023: 20.0}, 25.0),
                                 ({2023: 40.0}, 5.0), ({2023: 5.0}, 30.0)):
            with self.subTest(claimed=claimed):
                r = _row(self.ledger(y2023=45.0, claimed=claimed, back=0.0,
                                     closing=30.0), 2023)
                self.assertEqual(r["available_to_apply"], offered)

    @rule(R.CA_CARRY_04)
    def test_an_entry_above_the_gain_takes_nothing_the_lock_gave_back(self):
        led = self.ledger(claimed={2021: 40.0, 2023: 50.0})
        r = _row(led, 2023)
        self.assertEqual((r["carried_back"], r["carried_back_to"]),
                         (30.0, {"2021": 30.0}))
        self.assertEqual(led["unmatched_claims"], 40.0)

    @rule(R.CA_CARRY_04)
    def test_in_a_loss_year_the_lock_s_carry_back_stands(self):
        led = build_canada_ledger(
            _nets(y2021=100.0, y2023=-50.0), {2021: 40.0, 2023: 20.0},
            carried={2023: 10.0}, locked_back={2023: 40.0},
            locked_applied={2023: 0.0}, own_year=2023)
        r = _row(led, 2023)
        self.assertEqual((r["carried_back"], r["carryforward_balance"]), (40.0, 10.0))

    @rule(R.CA_CARRY_04)
    def test_what_the_lock_applied_counts_above_the_ledger_s_net(self):
        # The return's gain can be more than the books' (the FX gain on cash).
        r = _row(self.ledger(applied=30.0, back=20.0), 2023)
        self.assertEqual((r["carried_back"], r["claimed_applied"],
                          r["rebuilt_balance"]), (20.0, 50.0, 0.0))

    @rule(R.CA_CARRY_04)
    def test_a_claim_added_after_the_close_still_comes_off(self):
        # The lock took no carry-back; what the return left is the claim's.
        r = _row(self.ledger(claimed={2021: 10.0}, back=0.0, closing=30.0), 2023)
        self.assertEqual((r["carried_back"], r["carryforward_balance"]), (10.0, 20.0))

    @rule(R.CA_CARRY_04)
    def test_a_lock_that_really_applied_more_is_still_named(self):
        led = build_canada_ledger(
            _nets(y2022=-10.0, y2023=20.0), {},
            carried={2022: 10.0, 2023: 0.0},
            locked_back={2022: 0.0, 2023: 0.0},
            locked_applied={2022: 0.0, 2023: 20.0}, own_year=2023)
        self.assertEqual(_row(led, 2023)["applied_over_balance"],
                         {"applied": 20.0, "carried_in": 10.0})

class TestAClaimNoLossMeets(_Ledger):
    def test_inside_its_window_it_warns_and_says_a_loss_can_still_meet_it(self):
        from taxjson.bin import taxjson_carryover as C
        led = self.ledger()
        self.assertEqual(led["claims_waiting"], {"2021": 10.0})
        self.assertEqual(led["unmatched_claims"], 10.0)
        self.assertNotIn("claims_beyond", led)
        self.assertIn("10.00 claimed for 2021 beyond the losses this history supports; "
                      "a loss by 2024 can still meet it", C.render_brief(led, "CAD", 2021))
        text = " ".join(C.render(led, "CAD", 2021, claimed_used=True,
                                 in_project=True).split())
        self.assertIn("warning: 10.00 of", text.lower())
        self.assertIn("A loss of a year up to 2024 can still carry back to 2021", text)
        self.assertIn("check taxjson.toml's [carryover] claimed.", text)

    def test_past_its_window_it_does_not(self):
        from taxjson.bin import taxjson_carryover as C
        led = build_canada_ledger(_nets(y2021=100.0, y2024=20.0), {2021: 40.0})
        self.assertNotIn("claims_waiting", led)
        self.assertEqual(led["claims_beyond"], 40.0)
        self.assertNotIn("still meet it", C.render_brief(led, "CAD", 2021))

    def test_the_two_are_told_apart(self):
        from taxjson.bin import taxjson_carryover as C
        led = build_canada_ledger(_nets(y2020=100.0, y2021=100.0, y2023=20.0),
                                  {2020: 15.0, 2021: 40.0})
        self.assertEqual(led["claims_waiting"], {"2021": 40.0})
        self.assertEqual((led["claims_beyond"], led["unmatched_claims"]), (15.0, 55.0))
        brief = C.render_brief(led, "CAD", 2020)
        self.assertIn("40.00 claimed for 2021 beyond", brief)
        self.assertIn("15.00 claimed beyond", brief)

    def test_the_window_is_read_from_the_project_year(self):
        nets, claim = _nets(y2021=100.0, y2023=20.0), {2021: 40.0}
        self.assertEqual(build_canada_ledger(nets, claim)["claims_waiting"], {"2021": 40.0})
        led = build_canada_ledger(nets, claim, through_year=2025)
        self.assertNotIn("claims_waiting", led)
        self.assertEqual(led["claims_beyond"], 40.0)
        # ...and from an earlier project year, whatever later rows there are.
        led = build_canada_ledger(_nets(y2021=100.0, y2024=20.0), claim, through_year=2023)
        self.assertEqual(led["claims_waiting"].keys(), {"2021"})

    def test_cents_of_rounding_make_no_line(self):
        led = build_canada_ledger(_nets(y2021=1.0, y2022=1.0), {2021: 10.004, 2022: 10.004})
        self.assertEqual(led["unmatched_claims"], 20.01)
        self.assertEqual(led["claims_waiting"], {"2021": 10.0, "2022": 10.0})
        self.assertNotIn("claims_beyond", led)


class TestTheNextProject(unittest.TestCase):
    """The same closed year, read from the project after it (TJ-1547)."""
    @rule(R.CA_CARRY_04)
    def test_its_own_entry_leaves_nothing_pending_under_it(self):
        led = build_canada_ledger(
            _nets(y2021=100.0, y2022=-50.0, y2023=20.0, y2024=-30.0),
            {2021: 40.0, 2023: 20.0},
            carried={2022: 50.0, 2023: 0.0},
            locked_back={2022: 0.0, 2023: 30.0},
            locked_applied={2022: 0.0, 2023: 20.0})
        r = _row(led, 2023)
        self.assertEqual((r["carried_back"], r["claimed_applied"],
                          r["rebuilt_balance"]), (30.0, 50.0, 0.0))
        r = _row(led, 2024)
        self.assertEqual((r["carried_back"], r["carried_back_to"],
                          r["carryforward_balance"]),
                         (10.0, {"2021": 10.0}, 20.0))


class TestAnEntryAboveWhatTheLockApplied(unittest.TestCase):
    """2022 loss locked, a 2023 gain, a 2024 loss, read from the 2024 project: the
    rest of a 2023 entry waits for that loss, on a balance the return already used
    or still holds (TJ-1555, TJ-1558)."""
    def _led(self, net, claimed, carried_in, closing, applied=20.0, **kw):
        return build_canada_ledger(
            _nets(y2021=100.0, y2022=-carried_in, y2023=net, y2024=-kw.pop("loss")),
            claimed, carried={2022: carried_in, 2023: closing},
            locked_back={2022: 0.0, 2023: kw.pop("back", 0.0)},
            locked_applied={2022: 0.0, 2023: applied})

    def _both(self, led):
        return (_row(led, 2024)["carried_back"], _row(led, 2024)["carryforward_balance"])

    @rule(R.CA_CARRY_04)
    def test_the_return_used_the_balance(self):
        led = self._led(40.0, {2023: 35.0}, 20.0, 0.0, loss=15.0)
        self.assertEqual(_row(led, 2023)["claimed_applied"], 20.0)
        self.assertEqual(self._both(led), (15.0, 0.0))
        self.assertEqual(_row(led, 2024)["carried_back_to"], {"2023": 15.0})

    @rule(R.CA_CARRY_04)
    def test_with_an_earlier_claim_too(self):
        led = self._led(40.0, {2021: 30.0, 2023: 35.0}, 50.0, 0.0, back=30.0, loss=15.0)
        self.assertEqual(_row(led, 2023)["carried_back"], 30.0)
        self.assertEqual(self._both(led), (15.0, 0.0))

    @rule(R.CA_CARRY_04)
    def test_a_balance_left_doesn_t_erase_it(self):
        led = self._led(45.0, {2023: 40.0}, 50.0, 30.0, loss=20.0)
        self.assertEqual(_row(led, 2023)["rebuilt_balance"], 30.0)
        self.assertEqual(self._both(led), (20.0, 30.0))
        self.assertEqual(led["final_carryforward"], 30.0)

    @rule(R.CA_CARRY_04)
    def test_room_to_carry_back_follows_the_entry_as_the_offer_does(self):
        # No 2021 here: an offer would go to the earliest gain first.
        led = build_canada_ledger(
            _nets(y2022=-20.0, y2023=20.0, y2024=-15.0), {2023: 5.0},
            carried={2022: 20.0, 2023: 0.0}, locked_back={2022: 0.0, 2023: 0.0},
            locked_applied={2022: 0.0, 2023: 20.0})
        # The entry wins over the lock, for the offer as for the row (CA-CARRY-04).
        self.assertEqual(_row(led, 2023)["claimed_applied"], 5.0)
        self.assertEqual(_row(led, 2023)["available_to_apply"], 0.0)
        self.assertEqual(_row(led, 2024)["carryback_candidates"],
                         [{"year": 2023, "amount": 15.0}])

    @rule(R.CA_CARRY_04)
    def test_a_year_with_no_loss_or_a_smaller_one_can_t_meet_it(self):
        """The balance was carried past 2023, so it can't meet 2023's entry (TJ-1565)."""
        led = self._led(20.0, {2023: 40.0}, 50.0, 30.0, loss=-5.0)
        self.assertEqual(_row(led, 2024)["carried_back"], 0.0)
        self.assertEqual(_row(led, 2024)["carryforward_balance"], 30.0)
        self.assertEqual(led["claims_waiting"], {"2023": 20.0})
        led = self._led(20.0, {2023: 40.0}, 50.0, 30.0, loss=5.0)
        self.assertEqual(self._both(led), (5.0, 30.0))
        self.assertEqual(led["claims_waiting"], {"2023": 15.0})

    @rule(R.CA_CARRY_04)
    def test_a_lock_that_applied_nothing_leaves_the_whole_entry_to_a_later_loss(self):
        led = self._led(20.0, {2023: 15.0}, 50.0, 50.0, applied=0.0, loss=-5.0)
        self.assertEqual(_row(led, 2024)["carried_back"], 0.0)
        self.assertEqual(led["claims_waiting"], {"2023": 15.0})

    @rule(R.CA_CARRY_04)
    def test_a_later_year_s_loss_that_reached_the_balance_still_can(self):
        """A claim under a closed 2021 and a 2022 loss, met in 2023: 2022 is a later
        year than 2021, though not the year that meets it."""
        led = build_canada_ledger(
            _nets(y2021=100.0, y2022=-50.0, y2023=20.0), {2021: 40.0},
            carried={2021: 0.0, 2022: 50.0, 2023: 0.0},
            locked_back={2021: 0.0, 2022: 0.0, 2023: 30.0},
            locked_applied={2021: 0.0, 2022: 0.0, 2023: 20.0})
        self.assertEqual(_row(led, 2023)["carried_back"], 30.0)
        self.assertEqual(led["claims_waiting"], {"2021": 10.0})

    def test_the_cents_between_an_entry_and_its_lock_leave_nothing(self):
        def led(rows):
            nets = _nets(y2022=-120.0, y2023=99.98, y2024=-1.0)
            nets[2023]["dispositions"] = rows
            return build_canada_ledger(
                nets, {2023: 100.0}, carried={2022: 120.0, 2023: 20.02},
                locked_back={2022: 0.0, 2023: 0.0},
                locked_applied={2022: 0.0, 2023: 99.98})
        self.assertNotIn("unmatched_claims", led(10))
        self.assertEqual(_row(led(10), 2024)["carried_back"], 0.0)
        # Not on a year of too few rows for it to be cents.
        self.assertEqual(_row(led(1), 2024)["carried_back"], 0.02)

    @rule(R.CA_CARRY_04)
    def test_a_lock_that_records_no_applied_says_nothing_of_the_entry(self):
        led = build_canada_ledger(
            _nets(y2022=-50.0, y2023=45.0, y2024=-20.0), {2023: 40.0},
            carried={2022: 50.0, 2023: 10.0}, locked_back={2022: 0.0, 2023: 0.0})
        self.assertEqual(_row(led, 2023)["claimed_applied"], 40.0)
        self.assertEqual(_row(led, 2024)["carried_back"], 0.0)
        self.assertNotIn("unmatched_claims", led)


class TestAgainstAHandModel(unittest.TestCase):
    """Two years after a closed 2023, every lock as a close records it, on a model
    written apart from the fold: a claim's rest is met only by a later year's loss,
    oldest first; a 2021 claim's window ends in 2024; a closed gain uses the balance
    forward; an unclosed one, with no claim, doesn't."""
    GRID = itertools.product(
        (20.0, 50.0), (20.0, 45.0), (0.0, 30.0, 40.0), (0.0, 5.0, 20.0, 35.0, 40.0),
        (-5.0, -15.0, -30.0, 0.0, 5.0), (-5.0, -30.0, 0.0, 5.0), (False, True), (False, True))

    @rule(R.CA_CARRY_04)
    def test_the_grid(self):
        for loss22, gain, claim, entry, n24, n25, lock21, lock24 in self.GRID:
            used = min(gain, loss22)
            back = min(claim, loss22 - used)
            closing = loss22 - used - back
            rest21, rest23 = claim - back, max(0.0, entry - used)
            # 2024
            loss = max(0.0, -n24)
            to21 = min(loss, rest21)
            to23 = min(loss - to21, rest23)
            back24 = to21 + to23
            rest23 -= to23
            applied24 = min(max(0.0, n24), closing) if lock24 else 0.0
            closing24 = closing + loss - back24 - applied24
            # 2025: 2021's window has closed
            loss = max(0.0, -n25)
            back25 = min(loss, rest23)
            closing25 = closing24 + loss - back25
            claimed = {k: v for k, v in ((2021, claim), (2023, entry)) if v}
            carried = {2022: loss22, 2023: closing, **({2021: 0.0} if lock21 else {}),
                       **({2024: closing24} if lock24 else {})}
            lock_back = {2022: 0.0, 2023: back, **({2021: 0.0} if lock21 else {}),
                         **({2024: back24} if lock24 else {})}
            lock_applied = {2022: 0.0, 2023: used, **({2021: 0.0} if lock21 else {}),
                            **({2024: applied24} if lock24 else {})}
            led = build_canada_ledger(
                _nets(y2021=100.0, y2022=-loss22, y2023=gain, y2024=n24, y2025=n25),
                claimed, carried, lock_back, lock_applied)
            case = (loss22, gain, claim, entry, n24, n25, lock21, lock24)
            self.assertEqual((_row(led, 2024)["carried_back"],
                              _row(led, 2024)["carryforward_balance"]),
                             (back24, closing24), case)
            self.assertEqual((_row(led, 2025)["carried_back"],
                              _row(led, 2025)["carryforward_balance"]),
                             (back25, closing25), case)

    @rule(R.CA_CARRY_04)
    def test_the_project_year_s_own_row(self):
        for loss22, gain, claim, entry in itertools.product(
                (20.0, 50.0), (20.0, 45.0), (0.0, 30.0, 40.0), (0.0, 5.0, 20.0, 35.0, 40.0)):
            used = min(gain, loss22)
            back = min(claim, loss22 - used)
            closing = loss22 - used - back
            claimed = {k: v for k, v in ((2021, claim), (2023, entry)) if v}
            led = build_canada_ledger(
                _nets(y2021=100.0, y2022=-loss22, y2023=gain), claimed,
                {2022: loss22, 2023: closing}, {2022: 0.0, 2023: back},
                {2022: 0.0, 2023: used}, own_year=2023)
            r = _row(led, 2023)
            case = (loss22, gain, claim, entry)
            self.assertEqual((r["carried_back"], r["carryforward_balance"]),
                             (back, closing), case)
            self.assertEqual(r["rebuilt_balance"], closing, case)
            self.assertNotIn("applied_over_balance", r, case)
            self.assertEqual(led.get("unmatched_claims", 0.0),
                             (claim - back) + max(0.0, entry - used), case)


class TestWhatIsReadAsOfTheProjectYear(unittest.TestCase):
    def test_a_later_row_doesn_t_expire_a_claim(self):
        led = build_canada_ledger(_nets(y2021=100.0, y2023=5.0, y2025=5.0), {2021: 40.0},
                                  through_year=2023)
        self.assertEqual(led["claims_waiting"], {"2021": 40.0})
        self.assertNotIn("expired_claims", led)

    def test_a_partial_later_row_doesn_t_absorb_a_claim(self):
        led = build_canada_ledger(_nets(y2021=100.0, y2023=5.0, y2024=-40.0), {2021: 40.0},
                                  through_year=2023)
        self.assertEqual(led["claims_waiting"], {"2021": 40.0})

    def test_a_claim_past_its_window_is_said_once(self):
        from taxjson.bin import taxjson_carryover as C
        led = build_canada_ledger(_nets(y2021=100.0, y2025=5.0), {2021: 40.0}, through_year=2025)
        self.assertEqual(led["expired_claims"], {"2021": 40.0})
        self.assertNotIn("claims_beyond", led)
        self.assertEqual(led["unmatched_claims"], 40.0)
        text = " ".join(C.render(led, "CAD", 2021, claimed_used=True, in_project=True).split())
        self.assertNotIn("exceed the losses", text)
        self.assertIn("was never met", text)


class TestALockOverTheBalance(unittest.TestCase):
    @rule(R.CA_CARRY_04)
    def test_it_names_what_was_carried_in_not_what_the_claims_left(self):
        led = build_canada_ledger(
            _nets(y2021=100.0, y2022=-50.0, y2023=20.0), {2021: 40.0},
            carried={2022: 50.0, 2023: 0.0}, locked_back={2022: 0.0, 2023: 40.0},
            locked_applied={2022: 0.0, 2023: 20.0})
        self.assertNotIn("applied_over_balance", _row(led, 2023))
        led = build_canada_ledger(
            _nets(y2022=-10.0, y2023=20.0), {}, carried={2022: 10.0, 2023: 0.0},
            locked_back={2022: 0.0, 2023: 0.0}, locked_applied={2022: 0.0, 2023: 20.0})
        self.assertEqual(_row(led, 2023)["applied_over_balance"],
                         {"applied": 20.0, "carried_in": 10.0})


class TestAnEarlierClosedYear(_Ledger):
    @rule(R.CA_CARRY_04)
    def test_a_claim_it_gives_back_is_back_in_its_balance(self):
        led = build_canada_ledger(
            _nets(y2021=100.0, y2022=-50.0, y2023=0.0), {2021: 40.0},
            carried={2022: 50.0}, locked_back={2022: 0.0},
            locked_applied={2022: 0.0})
        r = _row(led, 2022)
        self.assertEqual((r["carried_back"], r["carryforward_balance"],
                          r["rebuilt_balance"]), (0.0, 50.0, 50.0))
        self.assertEqual(_row(led, 2023)["carried_back"], 40.0)


class TestEveryReader(unittest.TestCase):
    """2021 gain 100, 2022 loss 50 closed, a 2023 gain of 20, a claim of 40. Built
    once: each test closes 2023 on a copy."""
    @classmethod
    def setUpClass(cls):
        td = tempfile.TemporaryDirectory()
        cls.addClassCleanup(td.cleanup)
        cls.built = Path(td.name) / "built"
        _build_years(cls.built, {2023: "2021 = 40.00"}, _SALES, close=(2021, 2022))

    def _check(self, filed, back, applied, closing, replaces=()):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td) / "copy"
            shutil.copytree(self.built, top)
            y = top / "2023"
            for one in (*replaces, filed):
                args = ["close-year", "--yes", "--force"]
                if one is not None:
                    csv = top / "filed.csv"
                    csv.write_text("symbol,date,qty,proceeds,cost,gain\n"
                                   f"ZZQF,2023-06-02,10,900,{900 - one:.2f},{one:.2f}\n")
                    args += ["--filed-dispositions", csv]
                _ok(_cli(y, *args))
            ncl = json.loads((y / "filed" / "2023.json").read_text()
                             )["carryforwards"]["net_capital_loss"]
            self.assertEqual((float(ncl.get("carried_back") or 0.0), ncl["applied"],
                              ncl["closing"]), (back, applied, closing), "the lock")
            doc = json.loads(_ok(_cli(y, "carryover", "--json")).stdout)
            r = _row(doc, 2023)
            self.assertEqual((r["carried_back"], r["carryforward_balance"]),
                             (back, closing), "the row")
            self.assertNotIn("applied_over_balance", r)
            self.assertNotIn("more than the", " ".join(
                _ok(_cli(y, "carryover")).stdout.split()))
            _ok(_cli(y, "handoff"))

    @rule(R.CA_CARRY_04)
    def test_the_books(self):
        self._check(None, 30.0, 20.0, 0.0)

    @rule(R.CA_CARRY_04)
    def test_a_filed_gain_of_the_same(self):
        self._check(20.0, 30.0, 20.0, 0.0)

    @rule(R.CA_CARRY_04)
    def test_a_filed_gain_that_takes_it_all(self):
        self._check(100.0, 0.0, 50.0, 0.0)

    @rule(R.CA_CARRY_01)
    def test_a_second_close_is_not_held_to_the_lock_it_replaces(self):
        """The first lock applied 50: the second applies 20, so 30 is left for
        the claim (TJ-1473)."""
        self._check(20.0, 30.0, 20.0, 0.0, replaces=(100.0,))

    def test_a_later_project_reads_the_window_from_its_own_year(self):
        """Books end in 2023, the project is 2025: the 2021 claim's window closed
        in 2024, whatever the last row is (TJ-1557)."""
        with tempfile.TemporaryDirectory() as td:
            top = Path(td) / "copy"
            shutil.copytree(self.built, top)
            y = top / "2025"
            y.mkdir()
            (y / "taxjson.toml").write_text((top / "2023" / "taxjson.toml").read_text()
                                            .replace("year = 2023", "year = 2025")
                                            .replace("../2022/filed/2022.json",
                                                     "../2023/filed/2023.json"))
            _ok(_cli(top / "2023", "close-year", "--yes"))
            # More than the 2023 lock's 30 can meet, so that some of it is left unmet.
            (y / "taxjson.toml").write_text((y / "taxjson.toml").read_text().replace(
                "2021 = 40.00", "2021 = 90.00"))
            _ok(_cli(y, "run", "--no-input"))
            doc = json.loads(_ok(_cli(y, "carryover", "--json")).stdout)
            self.assertNotIn("claims_waiting", doc)
            self.assertEqual(doc["expired_claims"], {"2021": 60.0})
            self.assertNotIn("claims_beyond", doc)

    def test_books_past_the_project_year_leave_its_window_open(self):
        """Project 2023, books up to 2024: from 2023 a loss of 2024 can still meet
        the 2021 claim, though the last row is the year it closes (TJ-1557)."""
        with tempfile.TemporaryDirectory() as td:
            top = Path(td) / "copy"
            shutil.copytree(self.built, top)
            y = top / "2023"
            book = top / "inputs" / "margin" / "t.tt"
            book.write_text(book.read_text() + _trades(2024, "ZZQD", 520.0))
            (y / "taxjson.toml").write_text((y / "taxjson.toml").read_text().replace(
                "2021 = 40.00", "2021 = 90.00"))
            _ok(_cli(y, "run", "--no-input"))
            doc = json.loads(_ok(_cli(y, "carryover", "--json")).stdout)
            self.assertEqual(doc["rows"][-1]["year"], 2024)
            self.assertEqual(doc["claims_waiting"], {"2021": 40.0})
            self.assertNotIn("claims_beyond", doc)

    @rule(R.CA_CARRY_04)
    def test_a_lock_on_disk_with_no_applied_says_nothing_of_the_entry(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td) / "copy"
            shutil.copytree(self.built, top)
            old = top / "2023"
            toml = (old / "taxjson.toml").read_text()
            (old / "taxjson.toml").write_text(toml.replace("2021 = 40.00", "2023 = 30.00"))
            _ok(_cli(old, "close-year", "--yes"))
            lock = old / "filed" / "2023.json"
            doc = json.loads(lock.read_text())
            self.assertEqual(doc["carryforwards"]["net_capital_loss"].pop("applied"), 20.0)
            lock.write_text(json.dumps(doc))
            y = top / "2024"
            y.mkdir()
            (y / "taxjson.toml").write_text(
                toml.replace("year = 2023", "year = 2024")
                .replace("../2022/filed/2022.json", "../2023/filed/2023.json")
                .replace("2021 = 40.00", "2023 = 30.00"))
            _ok(_cli(y, "run", "--no-input"))
            doc = json.loads(_ok(_cli(y, "carryover", "--json")).stdout)
            self.assertTrue(_row(doc, 2023)["balance_from_lock"])
            self.assertEqual(_row(doc, 2023)["claimed_applied"], 30.0)
            self.assertNotIn("claims_waiting", doc)

    @rule(R.CA_CARRY_01)
    def test_on_books_is_what_the_books_say_with_no_lock_in_it(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td) / "copy"
            shutil.copytree(self.built, top)
            y = top / "2023"
            (y / "taxjson.toml").write_text((y / "taxjson.toml").read_text().replace(
                "2021 = 40.00", "2021 = 40.00, 2023 = 20.00"))
            _ok(_cli(y, "close-year", "--yes"))
            doc = json.loads(_ok(_cli(y, "carryover", "--on-books", "--json")).stdout)
            r = _row(doc, 2023)
            self.assertNotIn("balance_from_lock", r)
            # The whole balance: the close caps it (TJ-1473). As before a close.
            self.assertEqual((r["carried_back"], r["carryforward_balance"]), (40.0, 0.0))


class TestTheNextClose(unittest.TestCase):
    """A 2024 loss of 30 after that 2023, with every earlier lock readable: the
    rest of the claim, 10, is all it carries back (TJ-1547). With 2023's own entry
    this lock recorded back 20, closing 10. TJ-1554 is the same close when only
    the prior year's lock is read."""
    @rule(R.CA_CARRY_04)
    def test_the_lock_takes_the_rest_of_the_claim_only(self):
        claim = "2021 = 40.00, 2023 = 20.00"
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            _build_years(top, {2023: claim, 2024: claim},
                         {**_SALES, 2024: ("ZZQD", 470.0)},
                         close=(2021, 2022, 2023, 2024), all_locks=True)
            y = top / "2024"
            ncl = json.loads((y / "filed" / "2024.json").read_text()
                             )["carryforwards"]["net_capital_loss"]
            self.assertEqual((ncl["carried_back"], ncl["closing"]), (10.0, 20.0))
            _ok(_cli(y, "handoff"))


if __name__ == "__main__":
    unittest.main()
