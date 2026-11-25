"""TJ-1487, TJ-5061: a sale with an unknown cost before a lock still moves a
T1A split, through a [carryover] claimed amount the lock leaves pending.

A close-year lock restarts the carryover ledger's balance, so the rule that a
split rests on sales the ledger left out looked only after the last lock. A
claim still pending survives the lock, and so does a left-out sale's doubt on
it: here the 2022 sale's result can meet the 2021 claim in 2022 (2024 then
carries back nothing) or leave it to 2024's loss (100.00 carried back, as these
books read). No figure moves. The split is marked: close-year stops on it, or
with --force locks it and records the sale, the checklist asks for attention,
and the next year's handoff says the split can still move.

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
from _support.years_carryover_ledger import _TOML, _cli

_UNKNOWN_2022 = ('OPENING 2020-12-31 QZRX.TO 50 cost=unknown reason="synthetic"\n'
                 "BUYSELL 2022-03-01 09:30:00 QZRX.TO -50 CAD 2.00 100.00 0.00\n")
_GAIN_2021 = ("BUYSELL 2021-02-01 09:30:00 QZRA.TO 50 CAD 10.00 -500.00 0.00\n"
              "BUYSELL 2021-08-01 09:30:00 QZRA.TO -50 CAD 14.00 700.00 0.00\n")
_GAIN_2023 = ("BUYSELL 2023-02-01 09:30:00 QZRD.TO 10 CAD 10.00 -100.00 0.00\n"
              "BUYSELL 2023-08-01 09:30:00 QZRD.TO -10 CAD 11.00 110.00 0.00\n")
_LOSS_2024 = ("BUYSELL 2024-02-01 09:30:00 QZRB.TO 50 CAD 20.00 -1000.00 0.00\n"
              "BUYSELL 2024-08-01 09:30:00 QZRB.TO -50 CAD 10.00 500.00 0.00\n")
_CLAIM_2021 = "\n[carryover]\nclaimed = { 2021 = 100.00 }\n"


def _net(net, n=2):
    return {"net": net, "dispositions": n}


def _ledger(nets, claimed, left_out=None, **kw):
    from taxjson.bin.taxjson_carryover import build_canada_ledger
    return build_canada_ledger(nets, claimed, left_out=left_out, **kw)


def _row(doc, year):
    return next(x for x in doc["rows"] if x["year"] == year)


# The repro's history: a 2021 gain the claim is under, no 2022 row (its one sale
# is left out), a 2023 lock carrying 0, a 2024 loss of 500.
_NETS = {2021: _net(200.0), 2023: _net(10.0), 2024: _net(-500.0)}
_LOCK_2023 = {"carried": {2023: 0.0}, "locked_back": {2023: 0.0},
              "locked_applied": {2023: 0.0}, "through_year": 2024}


class TestTheLedgerFollowsEachClaim(unittest.TestCase):
    """bin/taxjson_carryover `_left_out_moves`: the doubt rides on the claim."""

    @rule(R.CA_CARRY_04)
    def test_the_two_histories_split_apart(self):
        """The entry's probe: with the 2022 sale a loss of 100, the 2022 fold
        meets the claim and 2024 carries back 0, 500 forward."""
        known = _ledger({**_NETS, 2022: _net(-100.0, 1)}, {2021: 100.0}, **_LOCK_2023)
        left = _ledger(_NETS, {2021: 100.0}, left_out={2022}, **_LOCK_2023)
        self.assertEqual((_row(known, 2024)["carried_back"],
                          _row(known, 2024)["carryforward_balance"]), (0.0, 500.0))
        self.assertEqual((_row(left, 2024)["carried_back"],
                          _row(left, 2024)["carryforward_balance"]), (100.0, 400.0))

    @rule(R.CA_CARRY_04)
    def test_a_claim_pending_past_the_lock_is_marked(self):
        doc = _ledger(_NETS, {2021: 100.0}, left_out={2022}, **_LOCK_2023)
        self.assertEqual(doc["carry_back_left_out"], {"2024": {"2021": [2022]}})

    @rule(R.CA_CARRY_04)
    def test_no_figure_moves(self):
        with_mark = _ledger(_NETS, {2021: 100.0}, left_out={2022}, **_LOCK_2023)
        without = _ledger(_NETS, {2021: 100.0}, **_LOCK_2023)
        self.assertNotIn("carry_back_left_out", without)
        self.assertEqual(with_mark["rows"], without["rows"])
        self.assertEqual(with_mark["final_carryforward"], without["final_carryforward"])

    @rule(R.CA_CARRY_04)
    def test_a_claim_the_sale_may_have_left_unmet(self):
        """The 2022 fold met the claim from a known loss: the sale's result can
        leave it pending, for 2024's loss to carry back to."""
        nets = {**_NETS, 2022: _net(-100.0)}
        doc = _ledger(nets, {2021: 100.0}, left_out={2022}, **_LOCK_2023)
        self.assertEqual(_row(doc, 2024)["carried_back"], 0.0)
        self.assertEqual(doc["carry_back_left_out"], {"2024": {"2021": [2022]}})

    def test_a_lock_that_says_what_its_year_applied_settles_its_own_entry(self):
        """TJ-1455 stays: the claim is under the locked year, whose entry its
        lock's APPLIED meets; the sale before it can't reach the claim."""
        doc = _ledger({2022: _net(100.0), 2023: _net(-100.0)}, {2022: 40.0},
                      left_out={2021}, carried={2022: 0.0}, locked_back={2022: 0.0},
                      locked_applied={2022: 0.0}, through_year=2023)
        self.assertNotIn("carry_back_left_out", doc)
        self.assertEqual(_row(doc, 2023)["carried_back"], 40.0)

    def test_a_lock_that_does_not_leaves_its_entry_to_the_balance(self):
        doc = _ledger({2022: _net(100.0), 2023: _net(-100.0)}, {2022: 40.0},
                      left_out={2021}, carried={2022: 0.0}, locked_back={2022: 0.0},
                      through_year=2023)
        self.assertEqual(doc["carry_back_left_out"], {"2023": {"2022": [2021]}})

    def test_controls_mark_nothing(self):
        for name, nets, claimed, left_out in (
                ("no sale left out", _NETS, {2021: 100.0}, set()),
                ("the claim out of reach", _NETS, {2020: 100.0}, {2022}),
                ("a later year's sale", _NETS, {2021: 100.0}, {2025}),
                ("no balance in the year", {**_NETS, 2024: _net(50.0)},
                 {2021: 100.0}, {2022})):
            with self.subTest(case=name):
                doc = _ledger(nets, claimed, left_out=left_out, **_LOCK_2023)
                self.assertNotIn("carry_back_left_out", doc)


class TestTheRule(unittest.TestCase):
    """lib/carry_back.py: a marked year counts, even at or before a lock."""
    DOC = {"rows": [{"year": 2023, "balance_from_lock": True},
                    {"year": 2024, "net_gain": -500.0, "carried_back": 100.0}],
           "incomplete_years": [2022],
           "excluded_unknown_cost": [{"year": 2022, "date": "2022-03-01",
                                      "symbol": "QZRX.TO", "account": "margin",
                                      "proceeds": 100.0}],
           "carry_back_left_out": {"2024": {"2021": [2022]}}}

    def test_the_lock_no_longer_clears_it(self):
        from taxjson.lib.carry_back import ledger_left_out, split_left_out
        unmarked = {k: v for k, v in self.DOC.items() if k != "carry_back_left_out"}
        self.assertIsNone(ledger_left_out(unmarked, 2024))
        for read in (ledger_left_out, split_left_out):
            with self.subTest(read=read.__name__):
                left = read(self.DOC, 2024)
                self.assertEqual(left["years"], [2022])
                self.assertEqual([s["symbol"] for s in left["sales"]], ["QZRX.TO"])

    def test_another_years_mark_is_not_this_ones(self):
        from taxjson.lib.carry_back import ledger_left_out
        doc = dict(self.DOC, carry_back_left_out={"2025": {"2021": [2022]}})
        self.assertIsNone(ledger_left_out(doc, 2024))

    def test_a_damaged_mark_is_refused_by_the_one_reader(self):
        from taxjson.lib.carry_back import ledger_left_out, left_out_marks
        for bad in ("x", {"2024": []}, {"2024": {"2021": []}},
                    {"2024": {"2021": ["2022"]}}, {"x": {"2021": [2022]}}):
            with self.subTest(mark=bad):
                doc = dict(self.DOC, carry_back_left_out=bad)
                with self.assertRaises(ValueError):
                    left_out_marks(doc)
                self.assertIsNone(ledger_left_out(doc, 2024))

    def test_the_one_reader_of_the_child_refuses_a_damaged_mark(self):
        import subprocess
        from unittest import mock
        from taxjson.cli.common import LedgerUnavailable, _ledger_doc
        doc = dict(self.DOC, carry_back_left_out={"2024": {"2021": "2022"}})
        done = subprocess.CompletedProcess([], 0, json.dumps(doc), "")
        with mock.patch("taxjson.pipeline.dispatch.run_cmd", return_value=done):
            with self.assertRaises(LedgerUnavailable) as e:
                _ledger_doc(Path("."))
            self.assertIn("carry_back_left_out", str(e.exception))
            done.stdout = json.dumps(self.DOC)
            self.assertEqual(_ledger_doc(Path("."))["carry_back_left_out"],
                             self.DOC["carry_back_left_out"])

    def test_the_record_and_its_words(self):
        from taxjson.lib.carry_back import incomplete_record, sales_text
        rec = incomplete_record({"years": [2022],
                                 "sales": self.DOC["excluded_unknown_cost"]})
        self.assertEqual(rec, {"years": [2022], "sales": [
            {"date": "2022-03-01", "symbol": "QZRX.TO", "account": "margin"}]})
        self.assertEqual(sales_text(rec), "2022-03-01 QZRX.TO in margin")
        many = {"sales": [{"symbol": f"QZR{c}.TO"} for c in "ABCDE"]}
        self.assertEqual(sales_text(many), "QZRA.TO, QZRB.TO, QZRC.TO, and 2 more")

    def test_the_checklist_step_asks(self):
        from taxjson.lib import checklist as CL
        from taxjson.lib.carry_back import SPLIT_ATTENTION
        doc = self.DOC

        class Ctx:
            year, root = 2024, Path(".")
            cfg = {"carryover": {"claimed": {"2021": 100.0}}}
            settings = Settings.from_table({"country": "canada"})

            def sub(self, *_a):
                return 0, json.dumps(doc), ""
        res = CL.d_carryover(Ctx())
        self.assertEqual(res.status, "attention")
        self.assertIn(SPLIT_ATTENTION, res.detail)
        self.assertIn("(2022)", res.detail)


def _project(top, unknown):
    (top / "inputs" / "margin").mkdir(parents=True)
    (top / "inputs" / "margin" / "t.tt").write_text(
        "# made-up trades\n" + _GAIN_2021 + (_UNKNOWN_2022 if unknown else "")
        + _GAIN_2023 + _LOSS_2024)
    for year, prior in ((2023, None), (2024, 2023), (2025, 2024)):
        d = top / str(year)
        d.mkdir()
        toml = _TOML.replace("year = 2023", f"year = {year}")
        if prior:
            toml = toml.replace('[accounts.margin]', f'prior_year_record = '
                                f'"../{prior}/filed/{prior}.json"\n\n[accounts.margin]')
        (d / "taxjson.toml").write_text(toml + _CLAIM_2021)


class _PastALock(unittest.TestCase):
    """2023 closed (its lock carries 0), then 2024 with its loss: what the
    commands say, run once for the class."""
    UNKNOWN = True

    @classmethod
    def setUpClass(cls):
        td = tempfile.mkdtemp(prefix="tj1487-")
        cls.addClassCleanup(shutil.rmtree, td, True)
        top = Path(td)
        _project(top, cls.UNKNOWN)
        cls.top = top
        for year, args in ((2023, ("run", "--no-input")),
                           (2023, ("close-year", "--yes")),
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

    def ncl(self):
        return self.lock["carryforwards"]["net_capital_loss"]

    def est(self):
        self.assertEqual(self.estimate.returncode, 0, self.estimate.stderr[-2000:])
        return json.loads(self.estimate.stdout)["estimate"]


class TestASalePastALock(_PastALock):

    def test_the_ledger_marks_the_claim(self):
        doc = json.loads(self.carryover.stdout)
        self.assertEqual(doc["incomplete_years"], [2022])
        self.assertEqual(doc["carry_back_left_out"], {"2024": {"2021": [2022]}})

    @rule(R.CA_CARRY_03)
    def test_the_estimate_keeps_its_figures_and_marks_them(self):
        est = self.est()
        self.assertEqual((est["losses_carried_back"], est["losses_carried_forward"]),
                         (100.0, 400.0))
        self.assertEqual(est["carry_back_incomplete"]["years"], [2022])
        self.assertIn("sales with an unknown cost left out, 2022",
                      " ".join(self.estimate.stderr.split()))

    @rule(R.CA_CARRY_03)
    def test_close_year_stops_and_names_the_sale(self):
        self.assertEqual(self.close.returncode, 1)
        self.assertFalse(self.locked_after_stop)
        text = " ".join(self.close.stderr.split())
        self.assertIn("the split of this year's loss leaves out sales with an "
                      "unknown cost (2022)", text)
        self.assertIn("2022-03-01 QZRX.TO (margin)", text)
        self.assertIn("The result of the 2022 sale can change how much of [carryover] "
                      "claimed 2021 is still unmet when this year folds", text)
        self.assertNotIn("this year's gain", text)

    @rule(R.CA_CARRY_03)
    def test_force_locks_the_split_as_computed_and_records_the_sale(self):
        self.assertEqual(self.forced.returncode, 0, self.forced.stderr[-2000:])
        ncl = self.ncl()
        self.assertEqual((ncl["carried_back"], ncl["closing"]), (100.0, 400.0))
        self.assertEqual(ncl["carry_back_incomplete"], {"years": [2022], "sales": [
            {"date": "2022-03-01", "symbol": "QZRX.TO", "account": "margin"}],
            "claims": {"2021": [2022]}})

    @rule(R.CA_CARRY_05)
    def test_the_next_handoff_says_the_split_can_move(self):
        from taxjson.lib.carryforward import handoff_issues
        res = handoff_issues(self.top / "2025", {"settings": Settings.from_table(
            {"country": "canada", "year": 2025})}, self.lock)
        note = " ".join(res.split_note.split())
        self.assertIn("The 2024 record's T1A carry-back split can still move", note)
        self.assertIn("(2022: 2022-03-01 QZRX.TO in margin)", note)
        self.assertIn("what 2024 carries back to [carryover] claimed 2021.", note)
        self.assertIn("re-close 2024 with --force", note)
        self.assertEqual(res.issues, [])

    def test_the_checklist_step_asks_on_this_ledger(self):
        from taxjson.lib import checklist as CL
        out = self.carryover.stdout

        class Ctx:
            year, root = 2024, Path(".")
            cfg = {"carryover": {"claimed": {"2021": 100.0}}}
            settings = Settings.from_table({"country": "canada"})

            def sub(self, *_a):
                return 0, out, ""
        res = CL.d_carryover(Ctx())
        self.assertEqual(res.status, "attention", res.detail)
        self.assertIn("(2022)", res.detail)


class TestTheSameBooksWithNoSaleLeftOut(_PastALock):
    """The control: nothing new, the same figures."""
    UNKNOWN = False

    @rule(R.CA_CARRY_03)
    def test_nothing_is_marked_and_the_close_goes_on(self):
        self.assertNotIn("carry_back_left_out", json.loads(self.carryover.stdout))
        est = self.est()
        self.assertNotIn("carry_back_incomplete", est)
        self.assertEqual(self.close.returncode, 0, self.close.stderr[-2000:])
        ncl = self.ncl()
        self.assertEqual((ncl["carried_back"], ncl["closing"]), (100.0, 400.0))
        self.assertNotIn("carry_back_incomplete", ncl)


if __name__ == "__main__":
    unittest.main()
