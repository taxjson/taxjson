"""TJ-1487, TJ-1488 (the review of their fix): a split is marked only where a
result of the sales left out can move it.

A sale with an unknown cost can only add a loss to its year, or shrink or
cancel the loss the year shows. The first fix followed an uncertain balance to
every claim it could reach, and stopped on splits no result can move: a
[carryover] claimed amount under 2023 met in full from a 2020 loss, with an old
2021 sale left out, stopped 2024 and the years after, though they carry back
nothing whatever that sale's result is, and said "this year's gain leaves out
sales" of a year with none of its own. The ledger now folds again on the
results the sales can have (bin/taxjson_carryover `_left_out_moves`) and marks
a claim only where the project year's carry-back to it moves; the words of the
stop follow the year's row and the sales (lib/carry_back `split_words`). Up to
8 years of such sales every combination is folded; past that every claim in
reach is named and the ledger says the split was not checked in full
(TJ-5291).

Synthetic data only: made-up tickers and accounts, amounts under a thousand.
"""
import itertools
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from _support.years_carryover_ledger import _TOML, _cli


def _net(net, n=2):
    return {"net": net, "dispositions": n}


def _ledger(nets, claimed, left_out=None, through_year=2024, **kw):
    from taxjson.bin.taxjson_carryover import build_canada_ledger
    return build_canada_ledger(nets, claimed, left_out=left_out,
                               through_year=through_year, **kw)


# The results a sale left out can have, as a change to its year's net: the
# brute fold the marks are checked against (a loss far past every figure too).
_RESULTS = (-1e6, -1000.0, -400.0, -150.0, -60.0, -20.0, 30.0, 500.0)


def _split(doc, year):
    row = next((x for x in doc["rows"] if x["year"] == year), {})
    return (row.get("carried_back", 0.0),
            json.dumps(row.get("carried_back_to") or None, sort_keys=True))


def _moves(nets, claimed, left_out, year=2024, **kw):
    """Whether some result of the sales left out moves `year`'s split: every
    combination of `_RESULTS`, folded without the marks."""
    from taxjson.bin.taxjson_carryover import build_canada_ledger
    base = _split(build_canada_ledger(nets, claimed, through_year=year, **kw), year)
    years = sorted(left_out)
    for combo in itertools.product((0.0, *_RESULTS), repeat=len(years)):
        n2 = {y: dict(v) for y, v in nets.items()}
        for y, dv in zip(years, combo):
            if dv:
                was = n2.get(y, _net(0.0, 0))
                n2[y] = _net(was["net"] + dv, was["dispositions"] + 1)
        if _split(build_canada_ledger(n2, claimed, through_year=year, **kw), year) != base:
            return True
    return False


# The review's shape: the claim under 2023 met in full on its own return from
# 2020's loss of 500, a 2021 sale left out, 2024 and 2025 gains.
_FS = {2020: _net(-500.0), 2023: _net(200.0), 2024: _net(10.0)}


class TestNoMoveNoMark(unittest.TestCase):

    @rule(R.CA_CARRY_04)
    def test_a_claim_met_in_full_from_a_known_loss_is_not_marked(self):
        for name, nets, year in (
                ("2024", _FS, 2024),
                ("2025", {**_FS, 2025: _net(10.0)}, 2025)):
            with self.subTest(year=name):
                doc = _ledger(nets, {2023: 100.0}, left_out={2021}, through_year=year)
                self.assertNotIn("carry_back_left_out", doc)
                self.assertFalse(_moves(nets, {2023: 100.0}, {2021}, year))

    @rule(R.CA_CARRY_04)
    def test_the_same_claim_partly_met_is_marked(self):
        """The control: 2020's loss of 50 leaves 50 of the claim to 2024's loss,
        and a 2021 loss can meet it first."""
        nets = {2020: _net(-50.0), 2023: _net(200.0), 2024: _net(-300.0)}
        doc = _ledger(nets, {2023: 100.0}, left_out={2021})
        self.assertEqual(doc["carry_back_left_out"], {"2024": {"2023": [2021]}})
        self.assertTrue(_moves(nets, {2023: 100.0}, {2021}))

    @rule(R.CA_CARRY_04)
    def test_no_figure_moves(self):
        nets = {2020: _net(-50.0), 2023: _net(200.0), 2024: _net(-300.0)}
        marked = _ledger(nets, {2023: 100.0}, left_out={2021})
        plain = _ledger(nets, {2023: 100.0})
        self.assertEqual(marked["rows"], plain["rows"])
        self.assertEqual(marked["final_carryforward"], plain["final_carryforward"])

    def test_a_year_with_no_loss_and_no_sale_of_its_own_is_never_marked(self):
        for claimed in ({2023: 100.0}, {2021: 50.0}, {2022: 40.0, 2023: 100.0}):
            with self.subTest(claimed=claimed):
                self.assertNotIn("carry_back_left_out",
                                 _ledger({2021: _net(100.0), 2022: _net(300.0),
                                          2023: _net(200.0), 2024: _net(10.0)},
                                         claimed, left_out={2021, 2022}))

    @rule(R.CA_CARRY_04)
    def test_two_losses_that_each_meet_the_claim(self):
        """Only both cancelled leave the claim to 2024: the years are folded
        together, among as many as `_LEFT_OUT_YEARS` of them."""
        from taxjson.bin.taxjson_carryover import _LEFT_OUT_YEARS
        nets = {2021: _net(-100.0), 2022: _net(-100.0), 2023: _net(100.0),
                2024: _net(-300.0)}
        for name, left in (("those two", {2021, 2022}),
                           ("among many", set(range(2010, 2008 + _LEFT_OUT_YEARS))
                            | {2021, 2022})):
            with self.subTest(left_out=name):
                doc = _ledger(nets, {2023: 100.0}, left_out=left)
                self.assertEqual(doc["carry_back_left_out"], {"2024": {"2023": [2021, 2022]}})
        self.assertTrue(_moves(nets, {2023: 100.0}, {2021, 2022}))
        self.assertFalse(_moves(nets, {2023: 100.0}, {2021}))

    @rule(R.CA_CARRY_04)
    def test_seven_years_three_known_losses_and_the_years_own_sale(self):
        """The review's seven.py: 2018-2020 losses meet the claim under 2021;
        with all three cancelled and 2024's own sale a loss, 2024 carries 100
        back. An unrelated eighth year changes nothing."""
        nets = {2018: _net(-100.0), 2019: _net(-100.0), 2020: _net(-100.0),
                2021: _net(300.0), 2024: _net(10.0)}
        for left in ({2016, 2017, 2018, 2019, 2020, 2024},
                     {2015, 2016, 2017, 2018, 2019, 2020, 2024}):
            with self.subTest(years=len(left)):
                doc = _ledger(nets, {2021: 100.0}, left_out=left)
                self.assertEqual(doc["carry_back_left_out"],
                                 {"2024": {"2021": [2018, 2019, 2020, 2024]}})
        known = _ledger({2018: _net(0.0, 3), 2019: _net(0.0, 3), 2020: _net(0.0, 3),
                         2021: _net(300.0), 2024: _net(-490.0, 3)}, {2021: 100.0})
        self.assertEqual(next(x for x in known["rows"] if x["year"] == 2024)
                         ["carried_back_to"], {"2021": 100.0})

    @rule(R.CA_CARRY_04)
    def test_eight_years_name_every_claim_that_moves(self):
        """The review's cost8: a holding of unknown cost sold every year
        2017-2024; cancelling 2017-2020's losses leaves 30 of the claim under
        2021 to 2024's loss, so 2021 is named with 2022 and 2023."""
        nets = {y: _net(-30.0) for y in (2017, 2018, 2019, 2020, 2024)}
        nets.update({y: _net(60.0) for y in (2021, 2022, 2023)})
        claimed = {2021: 40.0, 2022: 50.0, 2023: 60.0}
        doc = _ledger(nets, claimed, left_out=set(range(2017, 2025)))
        self.assertEqual(sorted(doc["carry_back_left_out"]["2024"]), ["2021", "2022", "2023"])
        self.assertNotIn("carry_back_left_out_unchecked", doc)
        cancelled = _ledger({**nets, **{y: _net(0.0, 3) for y in (2017, 2018, 2019, 2020)}},
                            claimed)
        self.assertEqual(next(x for x in cancelled["rows"] if x["year"] == 2024)
                         ["carried_back_to"], {"2021": 30.0})

    @rule(R.CA_CARRY_04)
    def test_past_eight_years_every_claim_in_reach_is_named_and_said(self):
        from taxjson.bin.taxjson_carryover import _LEFT_OUT_YEARS
        nets = {y: _net(-30.0) for y in (2016, 2017, 2018, 2019, 2020, 2024)}
        nets.update({y: _net(60.0) for y in (2021, 2022, 2023)})
        claimed = {2019: 10.0, 2021: 40.0, 2022: 50.0, 2023: 60.0}
        left = set(range(2016, 2025))
        self.assertEqual(len(left), _LEFT_OUT_YEARS + 1)
        doc = _ledger(nets, claimed, left_out=left)
        self.assertEqual(doc["carry_back_left_out"],
                         {"2024": {str(c): sorted(left) for c in (2021, 2022, 2023)}})
        self.assertEqual(doc["carry_back_left_out_unchecked"], {"2024": 9})
        self.assertEqual(doc["rows"], _ledger(nets, claimed)["rows"])

    def test_with_no_claim_nothing_is_folded_again(self):
        from unittest import mock
        from taxjson.bin import taxjson_carryover as C
        with mock.patch.object(C, "_carried_to") as probe:
            doc = _ledger({2023: _net(-100.0), 2024: _net(-50.0)}, {}, left_out={2023})
        probe.assert_not_called()
        self.assertNotIn("carry_back_left_out", doc)


# A few dozen small histories: the mark against the brute fold, each one.
_HISTORIES = (
    {2020: _net(-500.0), 2023: _net(200.0), 2024: _net(10.0)},
    {2020: _net(-50.0), 2023: _net(200.0), 2024: _net(-300.0)},
    {2021: _net(300.0), 2022: _net(-40.0), 2024: _net(-200.0)},
    {2020: _net(100.0), 2021: _net(-100.0), 2022: _net(300.0), 2024: _net(-500.0)},
    {2021: _net(200.0), 2023: _net(10.0), 2024: _net(-500.0)},
    {2022: _net(100.0), 2024: _net(10.0)},
)
_CLAIMS = ({2023: 100.0}, {2021: 100.0}, {2022: 40.0, 2023: 100.0})
_LEFT = ({2021}, {2022, 2024})


class TestTheMarkIsTheBruteFold(unittest.TestCase):

    @rule(R.CA_CARRY_04)
    def test_each_history(self):
        seen = set()
        for i, (nets, claimed, left) in enumerate(
                itertools.product(_HISTORIES, _CLAIMS, _LEFT)):
            with self.subTest(history=i, nets=nets, claimed=claimed, left_out=left):
                marked = "2024" in (_ledger(nets, claimed, left_out=left)
                                    .get("carry_back_left_out") or {})
                self.assertEqual(marked, _moves(nets, claimed, left))
                seen.add(marked)
        self.assertEqual(seen, {True, False})

    @rule(R.CA_CARRY_04)
    def test_each_history_past_a_lock(self):
        """2023 locked as these books close it: a 2022 sale reaches 2024 only
        through a claim the lock leaves pending."""
        from taxjson.bin.taxjson_carryover import build_canada_ledger
        seen = set()
        for i, (nets, claimed) in enumerate(itertools.product(_HISTORIES, _CLAIMS)):
            row = next((x for x in build_canada_ledger(
                {y: v for y, v in nets.items() if y <= 2023}, claimed,
                through_year=2023)["rows"] if x["year"] == 2023), None)
            if row is None:
                continue
            lock = {"carried": {2023: row["carryforward_balance"]},
                    "locked_back": {2023: row["carried_back"]}}
            with self.subTest(history=i, nets=nets, claimed=claimed):
                marked = "2024" in (_ledger(nets, claimed, left_out={2022}, **lock)
                                    .get("carry_back_left_out") or {})
                self.assertEqual(marked, _moves(nets, claimed, {2022}, **lock))
                seen.add(marked)
        self.assertEqual(seen, {True, False})

    @rule(R.CA_CARRY_04)
    def test_a_stated_carry_back_and_a_claim_partly_pending(self):
        """The review's unit-level shape: [carryover] carried_back states
        2022's, a 2022 sale left out, a claim under 2020 partly pending, a 2023
        gain: the sale's result moves 2023's carry-back, so it is marked."""
        nets = {2020: _net(100.0), 2021: _net(300.0), 2022: _net(-40.0), 2023: _net(300.0)}
        claimed, stated = {2020: 100.0}, {2022: 40.0}
        doc = _ledger(nets, claimed, left_out={2022}, through_year=2023, stated=stated)
        self.assertTrue(_moves(nets, claimed, {2022}, 2023, stated=stated))
        self.assertEqual(doc["carry_back_left_out"], {"2023": {"2020": [2022]}})
        self.assertEqual(doc["rows"], _ledger(nets, claimed, through_year=2023,
                                              stated=stated)["rows"])


class TestTheWords(unittest.TestCase):
    """lib/carry_back `split_words`: each sentence true of the row and the sales."""

    def words(self, years, year, loss, claims=None, undated=False):
        from taxjson.lib.carry_back import split_words
        left = {"years": years, "sales": [{"year": y, "symbol": "QZRX.TO"} for y in years],
                **({"claims": claims} if claims else {}),
                **({"undated": True} if undated else {})}
        said = split_words(left, year, loss)
        return said, " ".join([said.head, *said.why])

    def test_a_loss_year_and_a_sale_of_its_own(self):
        said, text = self.words([2024], 2024, True, {2023: [2024]})
        self.assertEqual(said.head, "the split of this year's loss leaves out sales with an "
                                    "unknown cost (2024)")
        self.assertIn("Its result can change this year's loss", text)
        self.assertNotIn("make a loss", text)
        self.assertEqual(said.locks, "split")

    def test_a_loss_year_and_an_earlier_sale_names_the_claim(self):
        said, text = self.words([2022], 2024, True, {2021: [2022]})
        self.assertIn("The result of the 2022 sale can change how much of [carryover] "
                      "claimed 2021 is still unmet when this year folds, so what this year "
                      "carries back to it may change.", text)
        self.assertNotIn("Its result", text)

    def test_a_gain_year_and_a_sale_of_its_own(self):
        said, text = self.words([2023], 2023, False, {2022: [2023]})
        self.assertEqual(said.head, "this year's gain leaves out sales with an unknown "
                                    "cost (2023)")
        self.assertIn("Its result can make a loss of this year", text)
        self.assertNotIn("this year's loss", text)
        self.assertEqual(said.locks, "year")
        self.assertIn("locking a gain", said.accepted)

    def test_a_year_with_no_loss_and_only_earlier_sales(self):
        said, text = self.words([2021, 2022], 2024, False, {2022: [2021], 2023: [2022]})
        self.assertEqual(said.head, "this year's carry-back rests on sales with an "
                                    "unknown cost (2021, 2022)")
        self.assertIn("The result of the 2021 and 2022 sales can change how much of "
                      "[carryover] claimed 2022 and 2023 is still unmet", text)
        self.assertIn("carries back to them may change", text)
        for untrue in ("this year's loss", "this year's gain", "make a loss"):
            self.assertNotIn(untrue, text)

    def test_a_sale_with_no_year(self):
        said, text = self.words([], 2024, True, undated=True)
        self.assertIn("A sale with no year may be this year's own.", text)
        said, text = self.words([], 2024, False, undated=True)
        self.assertIn("this year's carry-back rests on", said.head)
        self.assertIn("can make a loss of this year", text)

    def test_past_the_limit_the_words_say_it_was_not_checked_in_full(self):
        from taxjson.lib.carry_back import (incomplete_record, ledger_left_out,
                                            left_out_text, split_words)
        from taxjson.lib.carryforward import _split_note
        years = list(range(2016, 2025))
        doc = {"rows": [{"year": 2024, "net_gain": -30.0, "carried_back": 30.0}],
               "incomplete_years": years,
               "excluded_unknown_cost": [{"year": y, "symbol": "QZRX.TO"} for y in years],
               "carry_back_left_out": {"2024": {"2023": years}},
               "carry_back_left_out_unchecked": {"2024": 9}}
        left = ledger_left_out(doc, 2024)
        self.assertEqual(left["unchecked"], 9)
        self.assertEqual(left_out_text(left), "2016, 2017, 2018, 2019, 2020, and 4 more; "
                                              "9 years in all, not checked in full")
        said = split_words(left, 2024, True)
        self.assertIn("not checked in full", said.head)
        self.assertIn("With 9 years of such sales the ledger does not fold every result "
                      "they can have: it names every [carryover] claimed year this year can "
                      "carry back to, so the split may not in fact move.", said.why)
        rec = incomplete_record(left)
        self.assertEqual(rec["unchecked"], 9)
        self.assertIn("9 years in all, not checked in full",
                      _split_note({"carry_back_incomplete": rec}, 2024))
        within = dict(doc)
        del within["carry_back_left_out_unchecked"]
        self.assertNotIn("unchecked", ledger_left_out(within, 2024))

    def test_a_damaged_count_is_refused_by_the_one_reader(self):
        from taxjson.lib.carry_back import left_out_unchecked
        for bad in ("x", {"2024": 0}, {"2024": "9"}, {"x": 9}, {"2024": True}):
            with self.subTest(count=bad):
                with self.assertRaises(ValueError):
                    left_out_unchecked({"carry_back_left_out_unchecked": bad})
        self.assertEqual(left_out_unchecked({"carry_back_left_out_unchecked": {"2024": 9}}),
                         {2024: 9})

    def test_the_one_reader_of_the_child_refuses_a_damaged_count(self):
        import subprocess
        from unittest import mock
        from taxjson.cli.common import LedgerUnavailable, _ledger_doc
        doc = {"rows": [], "carry_back_left_out_unchecked": {"2024": -1}}
        done = subprocess.CompletedProcess([], 0, json.dumps(doc), "")
        with mock.patch("taxjson.pipeline.dispatch.run_cmd", return_value=done):
            with self.assertRaises(LedgerUnavailable) as e:
                _ledger_doc(Path("."))
        self.assertIn("carry_back_left_out_unchecked", str(e.exception))

    def test_the_lock_records_the_claims_and_handoff_names_them(self):
        from taxjson.lib.carry_back import incomplete_record
        from taxjson.lib.carryforward import _split_note
        rec = incomplete_record({"years": [2022], "claims": {2021: [2022]},
                                 "sales": [{"date": "2022-03-01", "symbol": "QZRX.TO"}]})
        self.assertEqual(rec["claims"], {"2021": [2022]})
        note = _split_note({"carry_back_incomplete": rec}, 2024)
        self.assertIn("what 2024 carries back to [carryover] claimed 2021.", note)
        older = _split_note({"carry_back_incomplete": {"years": [2022]}}, 2024)
        self.assertIn("what 2024 carries back to a [carryover] claimed year.", older)

    def test_a_filed_return_spares_its_own_sales_claims_too(self):
        from taxjson.lib.carry_back import filed_left_out
        left = {"years": [2022, 2024], "claims": {2021: [2022], 2023: [2024]},
                "sales": [{"year": 2022}, {"year": 2024}]}
        self.assertEqual(filed_left_out(left, 2024)["claims"], {2021: [2022]})
        self.assertNotIn("claims", filed_left_out(dict(left, claims={2023: [2024]}), 2024))


def _trade(sym, year, buy, sell, n=50):
    return (f"BUYSELL {year}-02-01 09:30:00 {sym} {n} CAD {buy / n:.2f} -{buy:.2f} 0.00\n"
            f"BUYSELL {year}-08-01 09:30:00 {sym} -{n} CAD {sell / n:.2f} {sell:.2f} 0.00\n")


_UNKNOWN_2021 = ('OPENING 2018-12-31 QZRX.TO 50 cost=unknown reason="synthetic"\n'
                 "BUYSELL 2021-03-01 09:30:00 QZRX.TO -50 CAD 2.00 100.00 0.00\n")


class _TheReviewsProject(unittest.TestCase):
    """The review's shape on the CLI: 2020 a loss of 500, the 2021 sale left
    out, 2023 a gain of 200 the claim of 100 is under, 2024 and 2025 gains
    of 10; `TYPED`: the balance also given in [estimate] other_losses."""
    TYPED = False

    @classmethod
    def setUpClass(cls):
        td = tempfile.mkdtemp(prefix="tj1487fs-")
        cls.addClassCleanup(shutil.rmtree, td, True)
        top = Path(td)
        (top / "inputs" / "margin").mkdir(parents=True)
        (top / "inputs" / "margin" / "t.tt").write_text(
            "# made-up trades\n" + _trade("QZRB.TO", 2020, 1000, 500) + _UNKNOWN_2021
            + _trade("QZRA.TO", 2023, 500, 700) + _trade("QZRD.TO", 2024, 100, 110, 10)
            + _trade("QZRE.TO", 2025, 100, 110, 10))
        for year, prior in ((2024, None), (2025, 2024)):
            d = top / str(year)
            d.mkdir()
            toml = _TOML.replace("year = 2023", f"year = {year}")
            if prior:
                toml = toml.replace('[accounts.margin]', f'prior_year_record = '
                                    f'"../{prior}/filed/{prior}.json"\n\n[accounts.margin]')
            if cls.TYPED and not prior:
                toml = toml.replace("other_income = 50000",
                                    "other_income = 50000\nother_losses = 400.00")
            (d / "taxjson.toml").write_text(toml + "\n[carryover]\nclaimed = { 2023 = 100.00 }\n")
        cls.out = {}
        for year, args in ((2024, ("run", "--no-input")), (2024, ("estimate",)),
                           (2024, ("close-year", "--yes")), (2025, ("run", "--no-input")),
                           (2025, ("carryover", "--json")), (2025, ("estimate",)),
                           (2025, ("close-year", "--yes"))):
            cls.out[year, args[0]] = _cli(top / str(year), *args, width="120")

    def test_nothing_is_marked_and_both_years_close(self):
        for year in (2024, 2025):
            with self.subTest(year=year):
                for cmd in ("run", "estimate", "close-year"):
                    r = self.out[year, cmd]
                    self.assertEqual(r.returncode, 0, f"{cmd}: {r.stderr[-2000:]}")
                    self.assertNotIn("may change", r.stderr)
                    self.assertNotIn("leaves out sales with an unknown cost", r.stderr)
                    self.assertNotIn("rests on sales with an unknown cost", r.stderr)
        self.assertNotIn("carry_back_left_out",
                         json.loads(self.out[2025, "carryover"].stdout))


class TestNineYearsOfSalesLeftOut(unittest.TestCase):
    """A holding of unknown cost sold every year 2016-2024, gains in the
    claimed years 2021-2023, small losses in the others: too many years to
    fold every result, so close-year names every claim in reach and says so."""

    @classmethod
    def setUpClass(cls):
        td = tempfile.mkdtemp(prefix="tj5291-")
        cls.addClassCleanup(shutil.rmtree, td, True)
        top = Path(td)
        (top / "inputs" / "margin").mkdir(parents=True)
        text = 'OPENING 2014-12-31 QZRX.TO 450 cost=unknown reason="synthetic"\n'
        text += "".join(f"BUYSELL {y}-03-01 09:30:00 QZRX.TO -50 CAD 2.00 100.00 0.00\n"
                        for y in range(2016, 2025))
        for i, y in enumerate(range(2016, 2025)):
            text += _trade(f"QZR{'ABCDEFGHJ'[i]}.TO", y, 500, 560 if y in (2021, 2022, 2023)
                           else 470)
        (top / "inputs" / "margin" / "t.tt").write_text("# made-up trades\n" + text)
        (top / "2024").mkdir()
        (top / "2024" / "taxjson.toml").write_text(
            _TOML.replace("year = 2023", "year = 2024")
            + "\n[carryover]\nclaimed = { 2021 = 40.00, 2022 = 50.00, 2023 = 60.00 }\n")
        r = _cli(top / "2024", "run", "--no-input")
        if r.returncode != 0:
            raise AssertionError(r.stdout[-2000:] + r.stderr[-2000:])
        cls.carryover = _cli(top / "2024", "carryover", "--json")
        cls.close = _cli(top / "2024", "close-year", "--yes", width="120")

    def test_every_claim_in_reach_is_named_and_the_count_said(self):
        doc = json.loads(self.carryover.stdout)
        self.assertEqual(sorted(doc["carry_back_left_out"]["2024"]), ["2021", "2022", "2023"])
        self.assertEqual(doc["carry_back_left_out_unchecked"], {"2024": 9})

    @rule(R.CA_CARRY_03)
    def test_close_year_stops_and_says_the_split_may_not_move(self):
        self.assertEqual(self.close.returncode, 1)
        text = " ".join(self.close.stderr.split())
        self.assertIn("leaves out sales with an unknown cost (2016, 2017, 2018, 2019, 2020, "
                      "and 4 more; 9 years in all, not checked in full)", text)
        self.assertIn("With 9 years of such sales the ledger does not fold every result", text)
        self.assertIn("so the split may not in fact move.", text)


class TestTheReviewsProject(_TheReviewsProject):
    """fs1: the balance from the books."""


class TestTheReviewsProjectTyped(_TheReviewsProject):
    """fs2: the balance typed as well."""
    TYPED = True


if __name__ == "__main__":
    unittest.main()
