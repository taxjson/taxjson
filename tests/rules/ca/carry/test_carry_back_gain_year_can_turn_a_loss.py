"""TJ-1488, TJ-5062: a gain on books that leave out a sale of the year is no
proof there is no loss to carry back.

The rule that a split rests on sales the ledger left out asked for a loss or a
carry-back in the year's row: a provisional gain of 10.00 with a sale of the
year left out passed as no split, though that sale's cost can make the year a
loss of 490.00, 40.00 of it carried back to the [carryover] claimed amount
under 2022 that is still pending. No figure moves. The year is marked:
close-year stops on it, or with --force locks it and records the sale, the
checklist asks for attention, and the next year's handoff says the split can
still move. A gain year with no claim in reach, or with no sale of its own left
out, says nothing new.

Synthetic data only: made-up tickers and accounts, amounts under a thousand.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.project.settings import Settings
from _support.years_carryover_ledger import _TOML, _cli, _GAIN_2022

_UNKNOWN_2023 = ('OPENING 2020-12-31 QZRX.TO 50 cost=unknown reason="synthetic"\n'
                 "BUYSELL 2023-03-01 09:30:00 QZRX.TO -50 CAD 2.00 100.00 0.00\n")
_GAIN_2023 = ("BUYSELL 2023-02-01 09:30:00 QZRD.TO 10 CAD 10.00 -100.00 0.00\n"
              "BUYSELL 2023-08-01 09:30:00 QZRD.TO -10 CAD 11.00 110.00 0.00\n")


def _net(net, n=2):
    return {"net": net, "dispositions": n}


def _ledger(nets, claimed, left_out=None, **kw):
    from taxjson.bin.taxjson_carryover import build_canada_ledger
    return build_canada_ledger(nets, claimed, left_out=left_out, through_year=2023, **kw)


def _row(doc, year):
    return next(x for x in doc["rows"] if x["year"] == year)


_NETS = {2022: _net(100.0), 2023: _net(10.0)}


class TestTheLedgerMarksTheGainYear(unittest.TestCase):
    """bin/taxjson_carryover `_left_out_moves`: the year's own left-out sale
    and a claim in reach of its loss."""

    @rule(R.CA_CARRY_04)
    def test_the_two_histories_split_apart(self):
        """The entry's probe: with the sale's cost given, the net is -490.00
        and 40.00 is carried back, 450.00 forward."""
        known = _ledger({**_NETS, 2023: _net(-490.0, 3)}, {2022: 40.0})
        left = _ledger(_NETS, {2022: 40.0}, left_out={2023})
        self.assertEqual((_row(known, 2023)["carried_back"],
                          _row(known, 2023)["carryforward_balance"]), (40.0, 450.0))
        self.assertEqual((_row(left, 2023)["carried_back"],
                          _row(left, 2023)["carryforward_balance"]), (0.0, 0.0))

    @rule(R.CA_CARRY_04)
    def test_the_gain_year_is_marked(self):
        doc = _ledger(_NETS, {2022: 40.0}, left_out={2023})
        self.assertEqual(doc["carry_back_left_out"], {"2023": {"2022": [2023]}})
        self.assertEqual(doc["rows"], _ledger(_NETS, {2022: 40.0})["rows"])

    def test_a_year_of_left_out_sales_alone_has_no_row_and_is_marked(self):
        doc = _ledger({2022: _net(100.0)}, {2022: 40.0}, left_out={2023})
        self.assertEqual([x["year"] for x in doc["rows"]], [2022])
        self.assertEqual(doc["carry_back_left_out"], {"2023": {"2022": [2023]}})

    def test_controls_mark_nothing(self):
        for name, claimed, left_out in (
                ("no sale left out", {2022: 40.0}, set()),
                ("a claim under the year itself", {2023: 5.0}, {2023}),
                ("a claim out of reach", {2019: 40.0}, {2023}),
                ("the sale an earlier year's", {2022: 40.0}, {2022}),
                ("a later year's", {2022: 40.0}, {2024})):
            with self.subTest(case=name):
                self.assertNotIn("carry_back_left_out",
                                 _ledger(_NETS, claimed, left_out=left_out))

    def test_a_claim_met_already_is_no_claim_to_carry_back_to(self):
        """The claim under 2021 was met in 2021, by 2020's loss: none is left
        for a loss of 2023 to carry back to."""
        doc = _ledger({2020: _net(-100.0), 2021: _net(100.0), 2023: _net(10.0)},
                      {2021: 100.0}, left_out={2023})
        self.assertNotIn("carry_back_left_out", doc)


class TestTheRule(unittest.TestCase):
    """lib/carry_back.py `split_left_out`: a gain row counts when marked."""
    GAIN = {"year": 2023, "net_gain": 10.0, "carried_back": 0.0}
    DOC = {"rows": [GAIN], "incomplete_years": [2023], "project_year": 2023,
           "excluded_unknown_cost": [{"year": 2023, "date": "2023-03-01",
                                      "symbol": "QZRX.TO", "account": "margin"}],
           "claims_waiting": {"2022": 40.0},
           "carry_back_left_out": {"2023": {"2022": [2023]}}}

    def split(self, doc, year=2023):
        from taxjson.lib.carry_back import split_left_out
        return split_left_out(doc, year)

    def test_a_marked_gain_year_counts(self):
        self.assertEqual(self.split(self.DOC)["years"], [2023])
        unmarked = {k: v for k, v in self.DOC.items() if k != "carry_back_left_out"}
        self.assertIsNone(self.split(unmarked))

    def test_a_filed_return_holds_the_years_own_sale(self):
        doc = dict(self.DOC, rows=[dict(self.GAIN, net_source="filed_return")])
        self.assertIsNone(self.split(doc))

    def test_a_sale_with_no_year_may_be_the_years_own(self):
        undated = [{"year": None, "date": "", "symbol": "QZRX.TO"}]
        doc = {"rows": [self.GAIN], "incomplete_years": [], "project_year": 2023,
               "excluded_unknown_cost": undated, "claims_waiting": {"2022": 40.0}}
        left = self.split(doc)
        self.assertEqual((left["years"], left["undated"]), ([], True))
        for name, waiting in (("none", {}), ("under the year", {"2023": 5.0}),
                              ("out of reach", {"2019": 40.0})):
            with self.subTest(claims_waiting=name):
                self.assertIsNone(self.split(dict(doc, claims_waiting=waiting)))
        self.assertIsNone(self.split(dict(doc, project_year=2024)))

    def test_the_third_years_claim_counts_only_beside_a_row(self):
        undated = [{"year": None, "symbol": "QZRX.TO"}]
        doc = {"rows": [self.GAIN], "project_year": 2023, "claims_beyond": 40.0,
               "excluded_unknown_cost": undated}
        self.assertIsNotNone(self.split(doc))
        self.assertIsNone(self.split(dict(doc, rows=[])))


def _project(top, unknown, claimed):
    (top / "inputs" / "margin").mkdir(parents=True)
    (top / "inputs" / "margin" / "t.tt").write_text(
        "# made-up trades\n" + _GAIN_2022 + _GAIN_2023
        + (_UNKNOWN_2023 if unknown else ""))
    for year, prior in ((2023, None), (2024, 2023)):
        d = top / str(year)
        d.mkdir()
        toml = _TOML.replace("year = 2023", f"year = {year}")
        if prior:
            toml = toml.replace('[accounts.margin]', f'prior_year_record = '
                                f'"../{prior}/filed/{prior}.json"\n\n[accounts.margin]')
        (d / "taxjson.toml").write_text(toml + f"\n[carryover]\nclaimed = {claimed}\n")


class _AGainYear(unittest.TestCase):
    """2023: a gain of 10.00 on these books, the claim under 2022 pending: what
    the commands say, run once for the class."""
    UNKNOWN = True
    CLAIMED = "{ 2022 = 40.00 }"

    @classmethod
    def setUpClass(cls):
        td = tempfile.mkdtemp(prefix="tj1488-")
        cls.addClassCleanup(shutil.rmtree, td, True)
        top = Path(td)
        _project(top, cls.UNKNOWN, cls.CLAIMED)
        cls.top = top
        y = top / "2023"
        r = _cli(y, "run", "--no-input")
        if r.returncode != 0:
            raise AssertionError(r.stdout[-2000:] + r.stderr[-2000:])
        cls.carryover = _cli(y, "carryover", "--json")
        cls.estimate = _cli(y, "estimate", "--json")
        cls.close = _cli(y, "close-year", "--yes", width="120")
        cls.locked_after_stop = (y / "filed" / "2023.json").exists()
        cls.forced = (cls.close if cls.close.returncode == 0 else
                      _cli(y, "close-year", "--yes", "--force", width="120"))
        lock = y / "filed" / "2023.json"
        cls.lock = json.loads(lock.read_text()) if lock.exists() else {}

    def ncl(self):
        return self.lock["carryforwards"]["net_capital_loss"]


class TestAGainYearWithASaleLeftOut(_AGainYear):

    def test_the_ledger_marks_the_year(self):
        doc = json.loads(self.carryover.stdout)
        self.assertEqual(doc["carry_back_left_out"], {"2023": {"2022": [2023]}})
        self.assertEqual(doc["claims_waiting"], {"2022": 40.0})

    @rule(R.CA_CARRY_03)
    def test_the_estimate_is_as_it_was(self):
        """No loss: the estimate builds no ledger (the ruling in notes); its
        warning on the sale left out stays."""
        est = json.loads(self.estimate.stdout)["estimate"]
        self.assertEqual((est["losses_unused"], est["losses_carried_back"]), (0.0, 0.0))
        self.assertNotIn("carry_back_incomplete", est)
        self.assertIn("unknown cost", self.estimate.stderr)

    @rule(R.CA_CARRY_03)
    def test_close_year_stops_and_names_the_sale(self):
        self.assertEqual(self.close.returncode, 1)
        self.assertFalse(self.locked_after_stop)
        text = " ".join(self.close.stderr.split())
        self.assertIn("this year's gain leaves out sales with an unknown cost "
                      "(2023)", text)
        self.assertIn("2023-03-01 QZRX.TO (margin)", text)
        self.assertIn("Its result can make a loss of this year", text)
        self.assertNotIn("this year's loss", text)
        self.assertIn("--force to lock the year as computed", text)

    @rule(R.CA_CARRY_03)
    def test_force_locks_the_year_as_computed_and_records_the_sale(self):
        self.assertEqual(self.forced.returncode, 0, self.forced.stderr[-2000:])
        self.assertIn("locking a gain that leaves out sales with an unknown cost "
                      "(2023)", " ".join(self.forced.stderr.split()))
        ncl = self.ncl()
        self.assertEqual((ncl["closing"], ncl.get("carried_back", 0.0)), (0.0, 0.0))
        self.assertEqual(ncl["carry_back_incomplete"], {"years": [2023], "sales": [
            {"date": "2023-03-01", "symbol": "QZRX.TO", "account": "margin"}],
            "claims": {"2022": [2023]}})

    @rule(R.CA_CARRY_05)
    def test_the_next_handoff_says_the_split_can_move(self):
        from taxjson.lib.carryforward import handoff_issues
        res = handoff_issues(self.top / "2024", {"settings": Settings.from_table(
            {"country": "canada", "year": 2024})}, self.lock)
        note = " ".join(res.split_note.split())
        self.assertIn("The 2023 record's T1A carry-back split can still move", note)
        self.assertIn("(2023: 2023-03-01 QZRX.TO in margin)", note)
        self.assertIn("what 2023 carries back to [carryover] claimed 2022.", note)

    def test_the_checklist_step_asks_on_this_ledger(self):
        from taxjson.lib import checklist as CL
        from taxjson.lib.carry_back import SPLIT_ATTENTION
        out = self.carryover.stdout

        class Ctx:
            year, root = 2023, Path(".")
            cfg = {"carryover": {"claimed": {"2022": 40.0}}}
            settings = Settings.from_table({"country": "canada"})

            def sub(self, *_a):
                return 0, out, ""
        res = CL.d_carryover(Ctx())
        self.assertEqual(res.status, "attention", res.detail)
        self.assertIn(SPLIT_ATTENTION, res.detail)
        self.assertIn("(2023)", res.detail)


class TestAGainYearWithNoClaimInReach(_AGainYear):
    """The control: the claim is under the year itself, none under the 3
    before it."""
    CLAIMED = "{ 2023 = 5.00 }"

    @rule(R.CA_CARRY_03)
    def test_nothing_is_marked_and_the_close_goes_on(self):
        self.assertNotIn("carry_back_left_out", json.loads(self.carryover.stdout))
        self.assertEqual(self.close.returncode, 0, self.close.stderr[-2000:])
        self.assertNotIn("carry_back_incomplete", self.ncl())
        self.assertNotIn("gain leaves out", self.close.stderr)


if __name__ == "__main__":
    unittest.main()
