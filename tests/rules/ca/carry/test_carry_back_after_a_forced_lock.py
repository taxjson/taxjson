"""TJ-1487, TJ-1488 (the review of their fix): a lock forced past a sale with an
unknown cost passes that sale's doubt on to the years after it.

2023 is a gain of 10 on books that leave out a 2023 sale, with the claim under
2022 pending: close-year stops, and --force locks it, recording
`carry_back_incomplete`. 2024's loss of 500 then carries 40 back to 2022. The
first fix took the 2023 lock's figures as final, so 2024 closed with no word,
though the 2023 sale's result can meet the claim in 2023 (2024 then carries
back nothing). A lock that records `carry_back_incomplete` is now folded as its
re-close would be (bin/taxjson_carryover `_left_out_years`, `_left_out_moves`):
2024 is marked, close-year stops on it naming the claim, and with --force the
2025 handoff says the split can still move. No figure moves.

The lock's balance stays its own in that fold, with the change of the books'
balance there added: a balance typed in [estimate] other_losses is in no book
(ch2: 2023 closes 290 from a typed 300, and 2024, a gain year, carries 40 back
to 2022 from it), and a carry-back of the project year to a claim the lock's
record names is marked on that record too.

Synthetic data only: made-up tickers and accounts, amounts under a thousand.
"""
import argparse
import json
import shutil
import tempfile
import types
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.project.settings import Settings
from _support.years_carryover_ledger import _TOML, _cli, _GAIN_2022


def _net(net, n=2):
    return {"net": net, "dispositions": n}


def _ledger(nets, claimed, **kw):
    from taxjson.bin.taxjson_carryover import build_canada_ledger
    return build_canada_ledger(nets, claimed, through_year=2024, **kw)


_NETS = {2022: _net(100.0), 2023: _net(10.0), 2024: _net(-500.0)}
# The 2023 lock as --force wrote it: nothing carried, back or forward.
_FORCED = {"carried": {2023: 0.0}, "locked_back": {2023: 0.0},
           "locked_applied": {2023: 0.0}}


class TestTheLedger(unittest.TestCase):

    @rule(R.CA_CARRY_04)
    def test_the_two_histories_split_apart(self):
        """The 2023 sale a loss of 100 once 2023 is closed again: 2023 meets
        the claim and 2024 carries back nothing."""
        known = _ledger({**_NETS, 2023: _net(-90.0, 3)}, {2022: 40.0})
        self.assertEqual(next(x for x in known["rows"] if x["year"] == 2024)
                         ["carried_back"], 0.0)
        locked = _ledger(_NETS, {2022: 40.0}, **_FORCED)
        self.assertEqual(next(x for x in locked["rows"] if x["year"] == 2024)
                         ["carried_back"], 40.0)

    @rule(R.CA_CARRY_04)
    def test_a_lock_forced_past_the_sale_is_folded_again(self):
        doc = _ledger(_NETS, {2022: 40.0}, left_out={2023}, reopen={2023: {}}, **_FORCED)
        self.assertEqual(doc["carry_back_left_out"], {"2024": {"2022": [2023]}})
        plain = _ledger(_NETS, {2022: 40.0}, **_FORCED)
        self.assertEqual(doc["rows"], plain["rows"])
        self.assertEqual(doc["final_carryforward"], plain["final_carryforward"])

    def test_a_lock_that_stands_says_nothing(self):
        """Without the record the lock's figures stand: its year's sale cannot
        move them (the books leave the year to its lock)."""
        self.assertNotIn("carry_back_left_out",
                         _ledger(_NETS, {2022: 40.0}, left_out=set(), **_FORCED))

    def test_a_claim_met_in_full_before_the_lock_is_not_marked(self):
        nets = {2021: _net(-100.0), **_NETS}
        doc = _ledger(nets, {2022: 40.0}, left_out={2023}, reopen={2023: {}},
                      **{**_FORCED, "locked_back": {2023: 0.0}})
        self.assertNotIn("carry_back_left_out", doc)

    @rule(R.CA_CARRY_04)
    def test_a_typed_balance_stays_the_locks(self):
        """ch2: 2024 carries 40 back to 2022 from the lock's typed 290; the
        2023 sale's result can meet the claim in 2023 instead."""
        nets = {**_NETS, 2024: _net(10.0)}
        lock = {"carried": {2023: 290.0}, "locked_back": {2023: 0.0},
                "locked_applied": {2023: 10.0}}
        plain = _ledger(nets, {2022: 40.0}, **lock)
        self.assertEqual(next(x for x in plain["rows"] if x["year"] == 2024)
                         ["carried_back_to"], {"2022": 40.0})
        for record in ({}, {2022: [2023]}):
            with self.subTest(claims_recorded=record):
                doc = _ledger(nets, {2022: 40.0}, left_out={2023}, reopen={2023: record}, **lock)
                self.assertEqual(doc["carry_back_left_out"], {"2024": {"2022": [2023]}})
                self.assertEqual(doc["rows"], plain["rows"])

    def test_the_locks_record_marks_a_carry_back_to_its_claim(self):
        from unittest import mock
        from taxjson.bin import taxjson_carryover as C
        nets = {**_NETS, 2024: _net(10.0)}
        lock = {"carried": {2023: 290.0}, "locked_back": {2023: 0.0},
                "locked_applied": {2023: 10.0}}
        with mock.patch.object(C, "_folds_that_move", side_effect=lambda *a: {}):
            named = _ledger(nets, {2022: 40.0}, left_out={2023},
                            reopen={2023: {2022: [2023]}}, **lock)
            unnamed = _ledger(nets, {2022: 40.0}, left_out={2023}, reopen={2023: {}}, **lock)
        self.assertEqual(named["carry_back_left_out"], {"2024": {"2022": [2023]}})
        self.assertNotIn("carry_back_left_out", unnamed)

    @rule(R.CA_CARRY_04)
    def test_the_sales_result_reaches_a_later_claim_through_the_balance(self):
        """A loss of 2023 re-closed carries forward and meets the claim under
        2024, which 2025's loss then cannot carry back to."""
        nets = {2022: _net(100.0), 2023: _net(10.0), 2024: _net(100.0), 2025: _net(-300.0)}
        from taxjson.bin.taxjson_carryover import build_canada_ledger
        doc = build_canada_ledger(nets, {2024: 50.0}, left_out={2023}, reopen={2023: {}},
                                  through_year=2025, **_FORCED)
        self.assertEqual(doc["carry_back_left_out"], {"2025": {"2024": [2023]}})


def _manual(*dates):
    return [{"date": d, "date_settle": d} for d in dates]


class TestTheYearsLeftOut(unittest.TestCase):
    """bin/taxjson_carryover `_left_out_years`: a lock recording
    `carry_back_incomplete` keeps its years."""
    REQ = types.SimpleNamespace(effective_tax_date=lambda: "trade")
    ARGS = argparse.Namespace(project_year=2024, filed_net=None)

    def years(self, manual, lock_info):
        from taxjson.bin.taxjson_carryover import _left_out_years
        return _left_out_years(manual, self.REQ, self.ARGS, lock_info)

    def test_a_forced_lock_keeps_its_year_and_is_folded_again(self):
        lock = {2023: {"carried": 0.0, "incomplete": [2023],
                       "incomplete_claims": {2022: [2023]}}}
        self.assertEqual(self.years(_manual("2023-03-01"), lock),
                         ({2023}, {2023: {2022: [2023]}}))

    def test_a_lock_naming_an_earlier_year(self):
        lock = {2023: {"carried": 0.0, "incomplete": [2022]}}
        self.assertEqual(self.years(_manual("2022-03-01"), lock), ({2022}, {2023: {}}))

    def test_controls(self):
        for name, manual, lock, want in (
                ("a lock with no record", _manual("2023-03-01"),
                 {2023: {"carried": 0.0}}, (set(), {})),
                ("the sale has a cost since", _manual(),
                 {2023: {"carried": 0.0, "incomplete": [2023]}}, (set(), {})),
                ("another year's sale", _manual("2022-03-01"),
                 {2023: {"carried": 0.0, "incomplete": [2023]}}, ({2022}, {}))):
            with self.subTest(case=name):
                self.assertEqual(self.years(manual, lock), want)

    def test_the_lock_reads_its_record(self):
        from taxjson.bin.taxjson_carryover import _canada_lock_carry
        ncl = {"closing": 0.0, "carried_back": 0.0,
               "carry_back_incomplete": {"years": [2023, True, "2022"],
                                         "claims": {"2022": [2023, "x"], "y": [2023]}}}
        got = _canada_lock_carry(Path("."), ncl, 2023)
        self.assertEqual((got["incomplete"], got["incomplete_claims"]), ([2023], {2022: [2023]}))
        del ncl["carry_back_incomplete"]
        self.assertNotIn("incomplete", _canada_lock_carry(Path("."), ncl, 2023))


_UNKNOWN_2023 = ('OPENING 2020-12-31 QZRX.TO 50 cost=unknown reason="synthetic"\n'
                 "BUYSELL 2023-03-01 09:30:00 QZRX.TO -50 CAD 2.00 100.00 0.00\n")
_GAIN_2023 = ("BUYSELL 2023-02-01 09:30:00 QZRD.TO 10 CAD 10.00 -100.00 0.00\n"
              "BUYSELL 2023-08-01 09:30:00 QZRD.TO -10 CAD 11.00 110.00 0.00\n")
_LOSS_2024 = ("BUYSELL 2024-02-01 09:30:00 QZRB.TO 50 CAD 20.00 -1000.00 0.00\n"
              "BUYSELL 2024-08-01 09:30:00 QZRB.TO -50 CAD 10.00 500.00 0.00\n")
_GAIN_2024 = ("BUYSELL 2024-02-01 09:30:00 QZRE.TO 10 CAD 10.00 -100.00 0.00\n"
              "BUYSELL 2024-08-01 09:30:00 QZRE.TO -10 CAD 11.00 110.00 0.00\n")


class _AfterAForcedLock(unittest.TestCase):
    """2023 closed with --force past its sale, then 2024 with its loss: what
    the commands say, run once for the class. `TYPED`: ch2, the 2023 project
    typing a balance of 300 carried in, and 2024 a gain of 10."""
    UNKNOWN = True
    TYPED = False

    @classmethod
    def setUpClass(cls):
        td = tempfile.mkdtemp(prefix="tj1487ch-")
        cls.addClassCleanup(shutil.rmtree, td, True)
        top = Path(td)
        (top / "inputs" / "margin").mkdir(parents=True)
        (top / "inputs" / "margin" / "t.tt").write_text(
            "# made-up trades\n" + _GAIN_2022 + _GAIN_2023
            + (_UNKNOWN_2023 if cls.UNKNOWN else "")
            + (_GAIN_2024 if cls.TYPED else _LOSS_2024))
        for year, prior in ((2023, None), (2024, 2023), (2025, 2024)):
            d = top / str(year)
            d.mkdir()
            toml = _TOML.replace("year = 2023", f"year = {year}")
            if prior:
                toml = toml.replace('[accounts.margin]', f'prior_year_record = '
                                    f'"../{prior}/filed/{prior}.json"\n\n[accounts.margin]')
            if cls.TYPED and year == 2023:
                toml = toml.replace("other_income = 50000",
                                    "other_income = 50000\nother_losses = 300.00")
            (d / "taxjson.toml").write_text(toml + "\n[carryover]\nclaimed = { 2022 = 40.00 }\n")
        cls.top = top
        for year, args in ((2023, ("run", "--no-input")),
                           (2023, ("close-year", "--yes", "--force")),
                           (2024, ("run", "--no-input"))):
            r = _cli(top / str(year), *args)
            if r.returncode != 0:
                raise AssertionError(r.stdout[-2000:] + r.stderr[-2000:])
        y = top / "2024"
        cls.carryover = _cli(y, "carryover", "--json")
        cls.estimate = _cli(y, "estimate", "--json")
        cls.close = _cli(y, "close-year", "--yes", width="120")
        cls.locked_after_stop = (y / "filed" / "2024.json").exists()
        cls.forced = (cls.close if cls.close.returncode == 0 else
                      _cli(y, "close-year", "--yes", "--force", width="120"))
        lock = y / "filed" / "2024.json"
        cls.lock = json.loads(lock.read_text()) if lock.exists() else {}
        cls.lock_2023 = json.loads((top / "2023" / "filed" / "2023.json").read_text())

    def ncl(self):
        return self.lock["carryforwards"]["net_capital_loss"]


class TestAfterAForcedLock(_AfterAForcedLock):

    def test_the_2023_lock_records_its_sale(self):
        self.assertEqual(self.lock_2023["carryforwards"]["net_capital_loss"]
                         ["carry_back_incomplete"]["years"], [2023])

    def test_the_ledger_marks_2024(self):
        doc = json.loads(self.carryover.stdout)
        self.assertEqual(doc["carry_back_left_out"], {"2024": {"2022": [2023]}})

    @rule(R.CA_CARRY_03)
    def test_the_estimate_keeps_its_figures_and_marks_them(self):
        self.assertEqual(self.estimate.returncode, 0, self.estimate.stderr[-2000:])
        est = json.loads(self.estimate.stdout)["estimate"]
        self.assertEqual((est["losses_carried_back"], est["losses_carried_forward"]),
                         (40.0, 460.0))
        self.assertEqual(est["carry_back_incomplete"]["years"], [2023])
        self.assertIn("carry-back of this year's loss may change",
                      " ".join(self.estimate.stderr.split()))

    @rule(R.CA_CARRY_03)
    def test_close_year_stops_and_names_the_claim(self):
        self.assertEqual(self.close.returncode, 1)
        self.assertFalse(self.locked_after_stop)
        text = " ".join(self.close.stderr.split())
        self.assertIn("the split of this year's loss leaves out sales with an unknown "
                      "cost (2023)", text)
        self.assertIn("- 2023-03-01 QZRX.TO (margin)", text)
        self.assertIn("The result of the 2023 sale can change how much of [carryover] "
                      "claimed 2022 is still unmet when this year folds, so what this year "
                      "carries back to it may change.", text)
        for untrue in ("this year's gain", "make a loss of this year",
                       "can change this year's loss"):
            self.assertNotIn(untrue, text)

    @rule(R.CA_CARRY_03)
    def test_force_locks_the_split_as_computed_and_records_the_claim(self):
        self.assertEqual(self.forced.returncode, 0, self.forced.stderr[-2000:])
        self.assertIn("locking a split of this year's loss that leaves out sales with an "
                      "unknown cost (2023) (--force)", " ".join(self.forced.stderr.split()))
        ncl = self.ncl()
        self.assertEqual((ncl["carried_back"], ncl["closing"]), (40.0, 460.0))
        self.assertEqual(ncl["carry_back_incomplete"], {"years": [2023], "sales": [
            {"date": "2023-03-01", "symbol": "QZRX.TO", "account": "margin"}],
            "claims": {"2022": [2023]}})

    @rule(R.CA_CARRY_05)
    def test_the_2025_handoff_says_the_split_can_move(self):
        from taxjson.lib.carryforward import handoff_issues
        res = handoff_issues(self.top / "2025", {"settings": Settings.from_table(
            {"country": "canada", "year": 2025})}, self.lock)
        note = " ".join(res.split_note.split())
        self.assertEqual(
            note, "The 2024 record's T1A carry-back split can still move: it was locked on "
                  "a carryover ledger that left out sales with an unknown cost (2023: "
                  "2023-03-01 QZRX.TO in margin), and their results can change what 2024 "
                  "carries back to [carryover] claimed 2022. Once they have a cost, re-close "
                  "2024 with --force: the part carried back may change.")
        self.assertEqual(res.issues, [])


class TestATypedBalanceAfterAForcedLock(_AfterAForcedLock):
    """ch2: 2023 closes 290 from the typed 300; 2024, a gain of 10, carries 40
    back to 2022 from that balance."""
    TYPED = True

    def test_the_2023_lock_holds_the_typed_balance(self):
        ncl = self.lock_2023["carryforwards"]["net_capital_loss"]
        self.assertEqual((ncl["closing"], ncl["carry_back_incomplete"]["claims"]),
                         (290.0, {"2022": [2023]}))

    def test_the_ledger_marks_2024(self):
        doc = json.loads(self.carryover.stdout)
        self.assertEqual(doc["carry_back_left_out"], {"2024": {"2022": [2023]}})
        row = next(x for x in doc["rows"] if x["year"] == 2024)
        self.assertEqual((row["carried_back"], row["carryforward_balance"]), (40.0, 250.0))

    @rule(R.CA_CARRY_03)
    def test_the_estimate_keeps_its_figures_and_marks_them(self):
        self.assertEqual(self.estimate.returncode, 0, self.estimate.stderr[-2000:])
        est = json.loads(self.estimate.stdout)["estimate"]
        # 10 of the 290 is applied to this year's gain: 240 forward.
        self.assertEqual((est["losses_carried_back"], est["losses_carried_forward"]),
                         (40.0, 240.0))
        self.assertEqual(est["carry_back_incomplete"]["years"], [2023])
        text = " ".join(self.estimate.stderr.split())
        self.assertIn("this year's carry-back may change", text)
        self.assertNotIn("this year's loss", text)

    @rule(R.CA_CARRY_03)
    def test_close_year_stops_and_names_the_claim(self):
        self.assertEqual(self.close.returncode, 1)
        self.assertFalse(self.locked_after_stop)
        text = " ".join(self.close.stderr.split())
        self.assertIn("this year's carry-back rests on sales with an unknown cost (2023)", text)
        self.assertIn("The result of the 2023 sale can change how much of [carryover] "
                      "claimed 2022 is still unmet when this year folds", text)
        for untrue in ("this year's loss", "this year's gain", "make a loss of this year"):
            self.assertNotIn(untrue, text)
        self.assertIn("--force to lock the year as computed", text)

    @rule(R.CA_CARRY_05)
    def test_force_records_it_and_the_2025_handoff_says_the_split_can_move(self):
        self.assertEqual(self.forced.returncode, 0, self.forced.stderr[-2000:])
        ncl = self.ncl()
        self.assertEqual((ncl["carried_back"], ncl["closing"]), (40.0, 240.0))
        self.assertEqual(ncl["carry_back_incomplete"]["claims"], {"2022": [2023]})
        from taxjson.lib.carryforward import handoff_issues
        res = handoff_issues(self.top / "2025", {"settings": Settings.from_table(
            {"country": "canada", "year": 2025})}, self.lock)
        note = " ".join(res.split_note.split())
        self.assertIn("The 2024 record's T1A carry-back split can still move", note)
        self.assertIn("what 2024 carries back to [carryover] claimed 2022.", note)


class TestTheSameChainWithNoSaleLeftOut(_AfterAForcedLock):
    """The control: the 2023 sale's cost known, the same figures, no word."""
    UNKNOWN = False

    @rule(R.CA_CARRY_03)
    def test_nothing_is_marked_and_the_close_goes_on(self):
        self.assertNotIn("carry_back_incomplete",
                         self.lock_2023["carryforwards"]["net_capital_loss"])
        self.assertNotIn("carry_back_left_out", json.loads(self.carryover.stdout))
        self.assertEqual(self.close.returncode, 0, self.close.stderr[-2000:])
        ncl = self.ncl()
        self.assertEqual((ncl["carried_back"], ncl["closing"]), (40.0, 460.0))
        self.assertNotIn("carry_back_incomplete", ncl)


if __name__ == "__main__":
    unittest.main()
