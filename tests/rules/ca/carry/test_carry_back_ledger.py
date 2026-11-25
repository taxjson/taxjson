"""TJ-1422: a carryover ledger that cannot be built is not "no carry-back".

close-year's lock takes a T1A carry-back out of the loss it carries forward.
The figure comes from `tjs estimate --json`, which builds the ledger itself, or
from a second build when a filed loss may take more of the claim. When the build
failed both read as 0.00, so the lock carried the whole loss and the next year
claimed the carried-back part again.

Synthetic data only: made-up tickers and accounts, amounts under a thousand.

Formerly: test_fix_tj1422_ledger
"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tax_rules import rule
from taxjson.rules import ids as R

from taxjson.project.settings import Settings
from _support.years_carryover_ledger import _TOML, _CLAIM, _cli, _GAIN_2022


_TRADES = """# made-up trades
BUYSELL 2023-02-01 09:30:00 ZZQB.TO 50 CAD 10.00 -500.00 0.00
BUYSELL 2023-08-01 09:30:00 ZZQB.TO -50 CAD 8.00 400.00 0.00
"""

# What fails the ledger and nothing else: the estimate stays real, and so does
# the carryover wrapper that runs this engine. A child process, so no patch of
# this test's own modules could reach it.
_BROKEN_LEDGER = """\
import sys
from taxjson.bin import taxjson_carryover as _c
def _main(argv=None):
    print("taxjson-carryover: error: the synthetic ledger failure",
          file=sys.stderr)
    return 2
_c.main = _main
"""


# A ledger that exits 0 with this output: no real input is known to print it, so
# these stand for an engine bug. Written raw: json.loads reads NaN, Infinity.
_FAKE_LEDGER = """\
import sys
from taxjson.bin import taxjson_carryover as _c
def _main(argv=None):
    sys.stdout.write(%r)
    return 0
_c.main = _main
"""
# A failure with nothing, or a lot, on stderr.
_NOISY_LEDGER = """\
import sys
from taxjson.bin import taxjson_carryover as _c
def _main(argv=None):
    sys.stderr.write(%r)
    return 2
_c.main = _main
"""


class _Project(unittest.TestCase):
    """A project of one year folder, run: its `TRADES` by account, its `TOML`,
    and folders for the child's PYTHONPATH that replace the carryover engine."""
    TRADES = {"margin": _TRADES}
    TOML = _TOML + _CLAIM
    YEAR = 2023

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.top = Path(td.name)
        for name, text in self.TRADES.items():
            (self.top / "inputs" / name).mkdir(parents=True)
            (self.top / "inputs" / name / "trades.tt").write_text(text)
        self.y = self.top / str(self.YEAR)
        self.y.mkdir()
        (self.y / "taxjson.toml").write_text(self.TOML)
        self.broken = self.engine("broken", _BROKEN_LEDGER)
        self.ok(_cli(self.y, "run", "--no-input"))

    def engine(self, name, source):
        folder = self.top / name
        folder.mkdir()
        (folder / "sitecustomize.py").write_text(source)
        return folder

    def fake(self, output):
        return self.engine("fake", _FAKE_LEDGER % (output,))

    def ok(self, r):
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        return r

    def lock(self):
        return json.loads(self.lock_path().read_text())

    def lock_path(self):
        return self.y / "filed" / f"{self.YEAR}.json"

    def estimate(self, **kw):
        return json.loads(self.ok(_cli(self.y, "estimate", "--json",
                                       **kw)).stdout)["estimate"]


class TestALedgerThatFails(_Project):

    @rule(R.CA_CARRY_03)
    def test_the_fixture_carries_back_with_a_working_ledger(self):
        """The control: the same project, the ledger working."""
        est = self.estimate()
        self.assertEqual(est["losses_unused"], 100.0)
        self.assertEqual(est["losses_carried_back"], 40.0)
        self.ok(_cli(self.y, "close-year", "--yes"))
        ncl = self.lock()["carryforwards"]["net_capital_loss"]
        self.assertEqual((ncl["carried_back"], ncl["closing"]), (40.0, 60.0))

    @rule(R.CA_CARRY_03)
    def test_close_year_stops_and_writes_no_lock(self):
        r = _cli(self.y, "close-year", "--yes", broken=self.broken)
        self.assertEqual(r.returncode, 1, r.stdout[-2000:] + r.stderr[-2000:])
        text = " ".join(r.stderr.split())
        self.assertIn("the carryover ledger could not be used", text)
        self.assertIn("the synthetic ledger failure", text)
        self.assertIn("Nothing was written", text)
        # TJ-1428: no input is known to be at fault.
        self.assertIn("Run `tjs carryover` to see why", text)
        self.assertNotIn("Fix the input", text)
        self.assertFalse((self.y / "filed" / "2023.json").exists())

    @rule(R.CA_CARRY_03)
    def test_force_keeps_the_lock_it_would_replace(self):
        self.ok(_cli(self.y, "close-year", "--yes"))
        before = (self.y / "filed" / "2023.json").read_bytes()
        r = _cli(self.y, "close-year", "--yes", "--force", broken=self.broken)
        self.assertEqual(r.returncode, 1, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertEqual((self.y / "filed" / "2023.json").read_bytes(), before)

    @rule(R.CA_CARRY_03)
    def test_estimate_json_leaves_the_split_out_and_says_why(self):
        r = self.ok(_cli(self.y, "estimate", "--json", broken=self.broken))
        est = json.loads(r.stdout)["estimate"]
        self.assertEqual(est["losses_unused"], 100.0)
        self.assertNotIn("losses_carried_back", est)
        self.assertNotIn("losses_carried_forward", est)
        text = " ".join(r.stderr.split())
        self.assertIn("carry-back of this year's loss unknown", text)
        self.assertIn("the synthetic ledger failure", text)

    @rule(R.CA_CARRY_03)
    def test_estimate_text_says_the_split_is_unknown(self):
        """TJ-1423: stdout too. A pipe keeps only that, and it read
        "100.00 (carry forward)", 40.00 too much."""
        r = self.ok(_cli(self.y, "estimate", broken=self.broken))
        self.assertIn("carry-back of this year's loss unknown",
                      " ".join(r.stderr.split()))
        line = next(ln for ln in r.stdout.splitlines()
                    if "Unused capital losses" in ln)
        self.assertNotIn("(carry forward", line)
        self.assertIn("the carry-forward is unknown", " ".join(line.split()))


class TestNoClaimNeedsNoLedger(_Project):
    TOML = _TOML

    @rule(R.CA_CARRY_03)
    def test_close_year_goes_on(self):
        """No claim under an earlier year: no carry-back to find, so the
        estimate does not build the ledger and a broken one stops nothing."""
        est = self.estimate(broken=self.broken)
        self.assertEqual(est["losses_carried_back"], 0.0)
        self.ok(_cli(self.y, "close-year", "--yes", broken=self.broken))
        ncl = self.lock()["carryforwards"]["net_capital_loss"]
        self.assertNotIn("carried_back", ncl)
        self.assertEqual(ncl["closing"], 100.0)


class TestAFiledLossTheBooksCap(unittest.TestCase):
    """The second build (TJ-0273): a filed loss above the books' one, with the
    estimate's figure capped there. A failure kept 0.00, dropping even the part
    the estimate had found."""

    _EST = {"losses_opening": 0.0, "losses_created": 100.0,
            "losses_applied": 0.0, "losses_unused": 100.0,
            "losses_carried_back": 100.0, "carry_sources": {}}
    _CFG = {"settings": Settings.from_table({"year": 2025, "country": "canada", "province": "ON"}),
            "carryover": {"claimed": {"2023": 300.0}}}

    def _fake(self, ledger):
        doc = {"estimate": self._EST, "filing": {"totals": {"gain": -100.0}},
               "totals": {"realized": -100.0}}

        def fake(argv, capture_output=False, **_k):
            if "carryover" in argv:
                return ledger
            return subprocess.CompletedProcess(argv, 0, json.dumps(doc), "")
        return fake

    def _block(self, ledger):
        from unittest import mock
        from taxjson.cli import file as cli_file
        with mock.patch("taxjson.pipeline.dispatch.run_cmd", self._fake(ledger)):
            return cli_file._carryforwards_for_lock(
                Path("."), self._CFG, filed_totals={"gain": -400.0})

    @rule(R.CA_CARRY_03)
    def test_a_ledger_that_exits_non_zero_stops_the_close(self):
        with self.assertRaises(SystemExit) as cm:
            self._block(subprocess.CompletedProcess([], 1, "", "boom"))
        self.assertIn("the carryover ledger could not be used",
                      str(cm.exception.code))

    @rule(R.CA_CARRY_03)
    def test_a_ledger_that_prints_no_ledger_stops_the_close(self):
        for out in ("not json", "[]", "{}", '{"rows": 3}'):
            with self.subTest(out=out), self.assertRaises(SystemExit):
                self._block(subprocess.CompletedProcess([], 0, out, ""))

    @rule(R.CA_CARRY_03)
    def test_a_working_ledger_takes_the_whole_claim(self):
        ledger = {"rows": [{"year": 2025, "carried_back": 300.0}]}
        ncl = self._block(subprocess.CompletedProcess(
            [], 0, json.dumps(ledger), ""))["net_capital_loss"]
        self.assertEqual((ncl["carried_back"], ncl["closing"]), (300.0, 100.0))


class TestTheLedgersRows(unittest.TestCase):
    """TJ-1424, TJ-1425: output that isn't a ledger is LedgerUnavailable. A
    ledger with no row for the year is an answer: a year with no dispositions
    and no return row has none."""

    def _back(self, out, code=0, stderr="", created=0.0, balance=0.0):
        from unittest import mock
        from taxjson.cli.common import _carry_back
        res = subprocess.CompletedProcess([], code, out, stderr)
        with mock.patch("taxjson.pipeline.dispatch.run_cmd", return_value=res):
            return _carry_back(Path("."), 2023, created, balance=balance)

    @rule(R.CA_CARRY_03)
    def test_no_row_with_a_balance_to_split_is_unknown(self):
        """TJ-1484: a carried-in balance can carry back in a year with no loss
        (TJ-1454), and only the year's row says whether it did. No row read as a
        confirmed carry-back of 0.00: the whole 500.00 carried forward, the claim
        unmet."""
        from taxjson.cli.common import LedgerUnavailable
        doc = json.dumps({"rows": [{"year": 2022, "carried_back": 0,
                                    "carryforward_balance": 500.0}]})
        with self.assertRaises(LedgerUnavailable) as cm:
            self._back(doc, balance=500.0)
        self.assertIn("no row for 2023, a year with a loss carried in", str(cm.exception))
        self.assertEqual(self._back(doc).amount, 0.0)

    @rule(R.CA_CARRY_04)
    def test_a_carry_back_the_ledger_can_t_tell_is_unknown(self):
        """TJ-1574: the row's carry-back rests on what a rebuilt year's return
        carried back, which nothing says: no figure to carry or lock."""
        from taxjson.cli.common import LedgerUnavailable
        fig = {"carried_back": 40.0, "carried_back_to": {"2021": 40.0}, "carryforward": 20.0}
        doc = {"rows": [{"year": 2022, "carried_back": 0.0, "carried_back_stated": 0.0},
                        {"year": 2023, "carried_back": 40.0}],
               "carry_back_cant_tell": {"years": [2021], "books": {"2021": 40.0},
                                        "answered": {"2022": 0.0}, "if_none": fig,
                                        "if_all": dict(fig, carried_back=0.0)}}
        with self.assertRaises(LedgerUnavailable) as cm:
            self._back(json.dumps(doc))
        self.assertIn("add `carried_back = { 2021 = 40.00, 2022 = 0.00 }` if it did",
                      str(cm.exception))
        self.assertIn("or `carried_back = { 2021 = 0, 2022 = 0.00 }` if not (then 40.00)",
                      str(cm.exception))
        del doc["carry_back_cant_tell"]
        back = self._back(json.dumps(doc))
        self.assertEqual((back.amount, back.stated), (40.0, {"2022": 0.0}))

    def test_no_row_for_the_year_is_no_carry_back(self):
        for doc in ({"rows": []},
                    {"rows": [{"year": 2022, "carried_back": 40}]}):
            with self.subTest(doc=doc):
                self.assertEqual(self._back(json.dumps(doc)).amount, 0.0)

    def test_a_row_of_the_year_is_read(self):
        doc = {"rows": [{"year": 2023, "carried_back": 40}]}
        self.assertEqual(self._back(json.dumps(doc)).amount, 40.0)

    def test_null_or_no_figure_is_0(self):
        for row in ({"year": 2023}, {"year": 2023, "carried_back": None}):
            with self.subTest(row=row):
                self.assertEqual(self._back(json.dumps({"rows": [row]})).amount,
                                 0.0)

    def test_figures_that_are_not_numbers_are_no_ledger(self):
        """TJ-1440: `or 0.0` ran before the check, so these read as 0.00, and
        true as 1.00. TJ-1441: carried_back_to had no finite check."""
        from taxjson.cli.common import LedgerUnavailable
        for row in ('{"year":2023,"carried_back":false}',
                    '{"year":2023,"carried_back":true}',
                    '{"year":2023,"carried_back":""}',
                    '{"year":2023,"carried_back":[]}',
                    '{"year":2023,"carried_back":{}}',
                    '{"year":2023,"carried_back":Infinity}',
                    '{"year":2022,"carried_back":false}',
                    '{"year":2023,"carryforward_balance":false}',
                    '{"year":true,"carried_back":40}',
                    '{"year":2023,"carried_back_to":{"2022":NaN}}',
                    '{"year":2023,"carried_back_to":{"2022":Infinity}}',
                    '{"year":2023,"carried_back_to":{"2022":true}}',
                    '{"year":2023,"carried_back_to":{"x":1}}',
                    '{"year":2023,"carried_back_to":[1]}',
                    '{"year":2023,"carried_back_to":false}'):
            with self.subTest(row=row), self.assertRaises(LedgerUnavailable):
                self._back('{"rows":[' + row + ']}')

    def test_no_row_when_this_year_made_the_loss_is_no_ledger(self):
        """TJ-1442: a loss of this year's sales has nets, so a row. A loss
        carried in with no sale this year (created 0) can have none."""
        from taxjson.cli.common import LedgerUnavailable
        doc = json.dumps({"rows": [{"year": 2022, "carried_back": 0}]})
        with self.assertRaises(LedgerUnavailable) as cm:
            self._back(doc, created=100.0)
        self.assertIn("2023", str(cm.exception))
        self.assertEqual(self._back(doc, created=0.0).amount, 0.0)
        self.assertEqual(self._back(
            json.dumps({"rows": [{"year": 2023, "carried_back": 40}]}),
            created=100.0).amount, 40.0)

    def test_the_reason_is_one_short_line(self):
        """TJ-1444: it goes in a sentence."""
        from taxjson.cli.common import LedgerUnavailable
        for err, want in (("", "exited 2 with no error output"),
                          ("".join(f"line {i} of noise here\n" for i in range(40)),
                           "line 39")):
            with self.subTest(err=err[:9]), self.assertRaises(
                    LedgerUnavailable) as cm:
                self._back("", code=2, stderr=err)
            why = str(cm.exception)
            self.assertNotIn("\n", why)
            self.assertLess(len(why), 300)
            self.assertIn(want, why)
            self.assertNotIn("(no error output)", why)

    def test_the_incomplete_years_are_those_at_or_before_the_year(self):
        """TJ-1436. Rows' own flag counts too; a later year moves nothing."""
        doc = {"rows": [{"year": 2023, "carried_back": 40},
                        {"year": 2020, "incomplete": True}],
               "incomplete_years": [2021, 2023, 2024],
               "excluded_unknown_cost": [
                   {"year": 2021, "date": "2021-03-01", "symbol": "ZZQE.TO"},
                   {"year": 2024, "date": "2024-03-01", "symbol": "ZZQH.TO"}]}
        inc = self._back(json.dumps(doc)).incomplete
        self.assertEqual(inc["years"], [2020, 2021, 2023])
        self.assertEqual([s["symbol"] for s in inc["sales"]], ["ZZQE.TO"])
        doc["incomplete_years"] = [2024]
        del doc["rows"][1]
        self.assertIsNone(self._back(json.dumps(doc)).incomplete)

    def test_incomplete_years_it_cannot_read_are_no_ledger(self):
        from taxjson.cli.common import LedgerUnavailable
        for bad in ("2021", ["2021"], [True], 2021):
            doc = {"rows": [], "incomplete_years": bad}
            with self.subTest(bad=bad), self.assertRaises(LedgerUnavailable):
                self._back(json.dumps(doc))

    def test_a_row_it_cannot_read_is_no_ledger(self):
        from taxjson.cli.common import LedgerUnavailable
        for row in (3, {"year": "2023", "carried_back": 40}, {"carried_back": 40},
                    {"year": 2023, "carried_back": "n/a"},
                    {"year": 2023, "carried_back": [1]},
                    {"year": 2023, "carried_back": float("nan")}):
            with self.subTest(row=row), self.assertRaises(LedgerUnavailable):
                self._back(json.dumps({"rows": [row]}))

    def test_books_left_out_are_no_ledger(self):
        from taxjson.cli.common import LedgerUnavailable
        doc = {"rows": [{"year": 2023, "carried_back": 40}],
               "missing_books": ["other"]}
        with self.assertRaises(LedgerUnavailable) as cm:
            self._back(json.dumps(doc))
        self.assertIn("other", str(cm.exception))


class TestAMissingBook(_Project):
    """TJ-1429: `tjs carryover` exits 0 with a book gone, and the claim then
    lands on the wrong year's loss. The book's loss covers the claim: 0.00
    carried back with it, 40.00 without."""
    TRADES = {"a": ("BUYSELL 2022-02-01 09:30:00 ZZQA.TO 10 CAD 50.00 -500.00 0.00\n"
                    "BUYSELL 2022-08-01 09:30:00 ZZQA.TO -10 CAD 60.00 600.00 0.00\n"
                    "BUYSELL 2023-02-01 09:30:00 ZZQC.TO 10 CAD 50.00 -500.00 0.00\n"
                    "BUYSELL 2023-08-01 09:30:00 ZZQC.TO -10 CAD 40.00 400.00 0.00\n"),
              "b": ("BUYSELL 2021-02-01 09:30:00 ZZQB.TO 10 CAD 50.00 -500.00 0.00\n"
                    "BUYSELL 2021-08-01 09:30:00 ZZQB.TO -10 CAD 46.00 460.00 0.00\n")}
    TOML = (_TOML.replace("[accounts.margin]", "[accounts.a]")
            + '[accounts.b]\ntype = "taxable"\n' + _CLAIM)

    def test_the_split_is_unknown_and_the_ledger_says_which(self):
        est = json.loads(_cli(self.y, "estimate", "--json").stdout)["estimate"]
        self.assertEqual(est["losses_carried_back"], 0.0)
        (self.y / "work" / "b_base.json").unlink()
        doc = json.loads(_cli(self.y, "carryover", "--json").stdout)
        self.assertEqual(doc["missing_books"], ["b"])
        # The estimate refuses books a full run did not leave (a's
        # wash-adjusted gains read b's book, now gone), as close-year does
        # (TJ-4347): no carry-back figure on the wrong year's loss. Before,
        # it left the figure out and named the missing book.
        r = _cli(self.y, "estimate", "--json")
        self.assertEqual(r.returncode, 1, r.stdout[-2000:])
        self.assertEqual(r.stdout, "")
        self.assertIn("b_base.json", " ".join(r.stderr.split()))

    def test_a_ledger_that_ran_is_not_called_failed(self):
        """TJ-1439: `tjs carryover` exits 0 with a book gone. At a normal
        width, so the reason has to fit the one line."""
        (self.y / "work" / "b_base.json").unlink()
        self.assertEqual(_cli(self.y, "carryover").returncode, 0)
        r = _cli(self.y, "estimate", width="120")
        self.assertNotIn("failed", r.stdout + r.stderr)
        # (Refused, TJ-4347: the books a full run left are gone.)
        self.assertEqual(r.returncode, 1, r.stdout)
        text = " ".join(r.stderr.split())
        self.assertIn("b_base.json", text)
        self.assertIn("`tjs run`", text)


class TestNoClaimNeedsNoFigure(unittest.TestCase):
    """TJ-1430: with no claim there is no carry-back to find, even from an
    estimate document without the figure."""

    def test_no_ledger_is_built(self):
        from unittest import mock
        from taxjson.cli import file as cli_file
        est = {"losses_opening": 0.0, "losses_created": 100.0,
               "losses_applied": 0.0, "losses_unused": 100.0,
               "carry_sources": {}}
        doc = {"estimate": est, "filing": {"totals": {"gain": -100.0}},
               "totals": {"realized": -100.0}}
        seen = []

        def fake(argv, capture_output=False, **_k):
            seen.append(argv)
            if "carryover" in argv:
                return subprocess.CompletedProcess(argv, 2, "", "boom")
            return subprocess.CompletedProcess(argv, 0, json.dumps(doc), "")
        cfg = {"settings": Settings.from_table({"year": 2025, "country": "canada",
                            "province": "ON"})}
        with mock.patch("taxjson.pipeline.dispatch.run_cmd", fake):
            ncl = cli_file._carryforwards_for_lock(Path("."), cfg)["net_capital_loss"]
        self.assertEqual(ncl["closing"], 100.0)
        self.assertFalse([a for a in seen if "carryover" in a])


class TestHandoffWithAFailedLedger(_Project):
    """TJ-1426: the whole claim was compared with the record, a false mismatch
    that blamed the inputs. The check says it could not run."""
    TRADES = {"margin": (
        "# made-up trades\n"
        "BUYSELL 2023-02-01 09:30:00 ZZQD.TO 10 CAD 10.00 -100.00 0.00\n"
        "BUYSELL 2023-08-01 09:30:00 ZZQD.TO -10 CAD 30.00 300.00 0.00\n"
        "BUYSELL 2024-02-01 09:30:00 ZZQE.TO 10 CAD 20.00 -200.00 0.00\n"
        "BUYSELL 2024-08-01 09:30:00 ZZQE.TO -10 CAD 10.00 100.00 0.00\n")}
    YEAR = 2024
    TOML = (_TOML.replace("year = 2023", "year = 2024")
            + '\n[carryover]\nclaimed = { 2023 = 40.00 }\n')

    def setUp(self):
        super().setUp()
        y23 = self.y23 = self.top / "2023"
        y23.mkdir()
        (y23 / "taxjson.toml").write_text(_TOML)
        self.ok(_cli(y23, "run", "--no-input"))
        self.ok(_cli(y23, "close-year", "--yes"))

    def handoff(self, *args, **kw):
        return _cli(self.y, "handoff", "--prior",
                    self.y23 / "filed" / "2023.json", *args, **kw)

    @rule(R.CA_CARRY_05)
    def test_working_the_claim_is_all_carried_back(self):
        r = self.handoff()
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertIn("Everything the closed year carried forward", r.stdout)

    @rule(R.CA_CARRY_05)
    def test_broken_it_says_the_check_could_not_run(self):
        r = self.handoff(broken=self.broken)
        self.assertEqual(r.returncode, 2, r.stdout[-2000:] + r.stderr[-2000:])
        text = " ".join(r.stdout.split())
        self.assertIn("could not be used", text)
        self.assertIn("the synthetic ledger failure", text)
        self.assertNotIn("2023 record: 0.00", text)
        self.assertNotIn("problem(s)", text)
        self.assertNotIn("Everything the closed year carried forward", text)
        self.assertIn("one check could not run", text)
        doc = json.loads(self.handoff("--json", broken=self.broken).stdout)
        self.assertEqual(doc["problems"], 0)
        self.assertEqual(doc["carry"], [])
        self.assertTrue(doc["carry_unchecked"])

    @rule(R.CA_CARRY_05)
    def test_broken_the_other_checks_still_run(self):
        (self.y / "taxjson.toml").write_text(self.TOML.replace(
            "other_income = 50000\n",
            "other_income = 50000\nother_losses = 7.00\n"))
        r = self.handoff(broken=self.broken)
        self.assertEqual(r.returncode, 1, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertIn("[estimate] other_losses", r.stdout)
        self.assertNotIn("[carryover] claimed 2023  here", r.stdout)

    @rule(R.CA_CARRY_05)
    def test_the_section_says_the_claim_was_not_compared(self):
        """TJ-1437: it printed (0) and OK. above the note."""
        out = self.handoff(broken=self.broken).stdout
        section = out[out.index("CARRY-FORWARD INPUTS VS THE 2023 RECORD"):]
        section = section[:section.index("\n\n")]
        self.assertIn("[carryover] claimed 2023", section)
        self.assertIn("not compared", section)
        self.assertNotIn("OK.", section)

    @rule(R.CA_CARRY_05)
    def test_a_comparison_that_could_not_apply_is_not_a_skipped_check(self):
        """TJ-1438: with no carryforwards block or no `applied` there is
        nothing to compare, whatever the ledger does."""
        path = self.y23 / "filed" / "2023.json"
        lock = json.loads(path.read_text())

        def no_block(doc):
            del doc["carryforwards"]

        def no_applied(doc):
            del doc["carryforwards"]["net_capital_loss"]["applied"]
        for name, change in (("block", no_block), ("applied", no_applied)):
            doc = json.loads(json.dumps(lock))
            change(doc)
            path.write_text(json.dumps(doc))
            with self.subTest(gone=name):
                self.assertEqual(self.handoff().returncode, 0)
                r = self.handoff(broken=self.broken)
                self.assertEqual(r.returncode, 0,
                                 r.stdout[-2000:] + r.stderr[-2000:])
                self.assertNotIn("could not run", r.stdout)
                self.assertNotIn("carry_unchecked",
                                 self.handoff("--json", broken=self.broken).stdout)

    @rule(R.CA_CARRY_05)
    def test_junk_from_the_ledger_is_a_check_that_could_not_run(self):
        """TJ-1441: NaN and Infinity brought the false mismatch back."""
        for num in ("NaN", "Infinity"):
            fake = self.engine(f"fake{num}", _FAKE_LEDGER % (
                '{"rows":[{"year":2024,"carried_back_to":{"2023":%s}}]}' % num,))
            with self.subTest(num=num):
                r = self.handoff(broken=fake)
                self.assertEqual(r.returncode, 2,
                                 r.stdout[-2000:] + r.stderr[-2000:])
                self.assertNotIn("2023 record: 0.00", r.stdout)
                self.assertNotIn("inf of it", r.stdout)

    @rule(R.CA_CARRY_05)
    def test_the_note_holds_the_reason_as_a_sentence(self):
        """TJ-1444: `((no error output))`, or many lines mid-sentence."""
        for name, err in (("none", ""),
                          ("many", "".join(f"noise {i}\n" for i in range(30)))):
            engine = self.engine(f"noisy{name}", _NOISY_LEDGER % (err,))
            with self.subTest(err=name):
                doc = json.loads(self.handoff("--json", broken=engine).stdout)
                note = next(n for n in doc["notes"]
                            if "was not compared" in n)
                self.assertNotIn("((", note)
                self.assertNotIn("\n", note)
                self.assertLess(len(note), 600)

    def test_the_checklist_shows_it_beside_other_problems(self):
        """TJ-1445: the problems branch returned first."""
        from taxjson.lib import checklist as CL

        class Ctx:
            year, root = 2024, self.y
            settings = Settings.from_table({"prior_year_record": "x"})

            def sub(self, *_a):
                return 1, json.dumps({"problems": 1, "positions": [{}],
                                      "carry_unchecked": True}), ""
        res = CL.d_handoff(Ctx())
        self.assertEqual(res.status, "attention")
        self.assertIn("1 opening position(s) differ", res.detail)
        self.assertIn("could not be compared", res.detail)

    def test_the_checklist_does_not_call_it_done(self):
        from taxjson.lib import checklist as CL

        class Ctx:
            year, root = 2024, self.y
            settings = Settings.from_table({"prior_year_record": "x"})

            def sub(self, *_a):
                return 2, json.dumps({"problems": 0,
                                      "carry_unchecked": True}), ""
        res = CL.d_handoff(Ctx())
        self.assertEqual(res.status, "attention")
        self.assertIn("could not be used", res.detail)


class TestCloseYearsNote(_Project):
    """TJ-1427: the TJ-0431 note was lost with nothing said. The lock is the
    same either way."""
    TRADES = {"margin": (
        "# made-up trades\n"
        "BUYSELL 2022-02-01 09:30:00 ZZQF.TO 10 CAD 20.00 -200.00 0.00\n"
        "BUYSELL 2022-08-01 09:30:00 ZZQF.TO -10 CAD 10.00 100.00 0.00\n"
        "BUYSELL 2023-02-01 09:30:00 ZZQG.TO 10 CAD 10.00 -100.00 0.00\n"
        "BUYSELL 2023-08-01 09:30:00 ZZQG.TO -10 CAD 12.00 120.00 0.00\n")}
    TOML = _TOML

    def test_it_says_the_check_could_not_run(self):
        r = _cli(self.y, "close-year", "--yes", broken=self.broken)
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        text = " ".join(r.stderr.split())
        self.assertIn("could not check", text)
        self.assertIn("the carryover ledger could not be used", text)
        broken_lock = (self.y / "filed" / "2023.json").read_text()
        r = _cli(self.y, "close-year", "--yes", "--force")
        self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertIn("these books carry a net capital loss of 100.00",
                      " ".join(r.stdout.split()))
        self.assertEqual(
            json.loads(broken_lock)["carryforwards"]["net_capital_loss"],
            json.loads((self.y / "filed" / "2023.json").read_text())
            ["carryforwards"]["net_capital_loss"])


class TestTheDefaultView(_Project):
    """TJ-1443, TJ-1439: at a normal width the details are dropped, so the
    headline holds the reason and the command. The tests above run at width 0,
    which is how this passed."""

    @rule(R.CA_CARRY_03)
    def test_the_estimates_warning_says_why_and_what_to_run(self):
        r = self.ok(_cli(self.y, "estimate", broken=self.broken, width="120"))
        text = " ".join(r.stderr.split())
        self.assertIn("the synthetic ledger failure", text)
        self.assertIn("`tjs carryover`", text)


class TestJunkReachesNoLock(_Project):
    """TJ-1440, TJ-1442, through the real CLI: each locked closing 100.00 where
    a working ledger has 60.00."""

    @rule(R.CA_CARRY_03)
    def test_close_year_stops(self):
        for name, out in (("false", '{"rows":[{"year":2023,"carried_back":false}]}'),
                          ("norow", '{"rows":[]}')):
            engine = self.engine(f"fake{name}", _FAKE_LEDGER % (out,))
            with self.subTest(out=out):
                r = _cli(self.y, "close-year", "--yes", "--force", broken=engine)
                self.assertEqual(r.returncode, 1,
                                 r.stdout[-2000:] + r.stderr[-2000:])
                self.assertFalse(self.lock_path().exists())
                self.assertNotIn("losses_carried_back", json.loads(
                    _cli(self.y, "estimate", "--json",
                         broken=engine).stdout)["estimate"])


_UNKNOWN_SALE = ('OPENING 2020-12-31 ZZQE.TO 50 cost=unknown reason="synthetic"\n'
                 "BUYSELL %s 09:30:00 ZZQE.TO -50 CAD 2.00 100.00 0.00\n")
# What a lock records of a split locked past that sale in 2021: its year, the
# sale by date, symbol and account, and the claimed year whose part carried
# back its result can move, for the next handoff (TJ-1487).
_LOCKED_2021 = {"years": [2021], "sales": [
    {"date": "2021-03-01", "symbol": "ZZQE.TO", "account": "margin"}],
    "claims": {"2022": [2021]}}


class TestAnIncompleteLedger(_Project):
    """TJ-1436, the owner's decision: a sale with an unknown cost is left out of
    the ledger and an excluded result can move the split (40.00 here; with this
    sale's cost known, a 40.00 loss, nothing is carried back). The ledger runs
    and says so, so the figures stay; close-year stops, and --force is the way
    past it because the state can be settled."""
    TRADES = {"margin": _GAIN_2022 + _TRADES + _UNKNOWN_SALE % "2021-03-01"}

    @rule(R.CA_CARRY_03)
    def test_estimate_json_keeps_the_figures_and_marks_them(self):
        r = self.ok(_cli(self.y, "estimate", "--json"))
        est = json.loads(r.stdout)["estimate"]
        self.assertEqual(est["losses_carried_back"], 40.0)
        self.assertEqual(est["carry_back_incomplete"]["years"], [2021])
        self.assertEqual(
            [s["symbol"] for s in est["carry_back_incomplete"]["sales"]],
            ["ZZQE.TO"])
        self.assertIn("unknown cost", " ".join(r.stderr.split()))
        self.assertIn("2021", r.stderr)

    @rule(R.CA_CARRY_03)
    def test_estimate_text_marks_the_line(self):
        """A pipe keeps only this line."""
        r = self.ok(_cli(self.y, "estimate", width="120"))
        lines = r.stdout.splitlines()
        at = next(i for i, ln in enumerate(lines)
                  if "Unused capital losses" in ln)
        line = " ".join(" ".join(lines[at:at + 2]).split())
        self.assertIn("40.00 carried back", line)
        self.assertIn("unknown cost", line)
        self.assertIn("2021", line)
        self.assertIn("unknown cost", " ".join(r.stderr.split()))

    @rule(R.CA_CARRY_03)
    def test_close_year_stops_and_names_the_sale(self):
        r = _cli(self.y, "close-year", "--yes", width="120")
        self.assertEqual(r.returncode, 1, r.stdout[-2000:] + r.stderr[-2000:])
        text = " ".join(r.stderr.split())
        for want in ("unknown cost", "ZZQE.TO", "2021-03-01", "--force",
                     "Nothing was written"):
            self.assertIn(want, text)
        self.assertFalse(self.lock_path().exists())

    @rule(R.CA_CARRY_03)
    def test_force_locks_the_split_and_warns(self):
        r = self.ok(_cli(self.y, "close-year", "--yes", "--force"))
        ncl = self.lock()["carryforwards"]["net_capital_loss"]
        self.assertEqual((ncl["carried_back"], ncl["closing"]), (40.0, 60.0))
        text = " ".join(r.stderr.split())
        self.assertIn("(--force)", text)
        self.assertIn("ZZQE.TO", text)

    def test_the_real_checklist_step_asks_for_attention(self):
        """TJ-1446: `tjs checklist --run` read `manual` in this project."""
        r = _cli(self.y, "checklist", "--run", width="120")
        text = " ".join(r.stdout.split())
        self.assertIn("unknown cost", text[text.index("carryover"):])
        self.assertRegex(text, r"\[!\] \d+\. carryover")


class TestAnIncompleteYearInTheProjectYear(_Project):
    """TJ-1436: the same with the sale in the year being closed."""
    TRADES = {"margin": _GAIN_2022 + _TRADES + _UNKNOWN_SALE % "2023-03-01"}

    @rule(R.CA_CARRY_03)
    def test_close_year_stops(self):
        r = _cli(self.y, "close-year", "--yes")
        self.assertEqual(r.returncode, 1, r.stdout[-2000:] + r.stderr[-2000:])
        self.assertIn("ZZQE.TO", " ".join(r.stderr.split()))


class TestAnIncompleteLedgerWithNoClaim(_Project):
    """No claim, no split: nothing stops and no ledger is read. A settled
    unknown cost keeps closing as it did."""
    TRADES = {"margin": _GAIN_2022 + _TRADES + _UNKNOWN_SALE % "2023-03-01"}
    TOML = _TOML

    @rule(R.CA_CARRY_03)
    def test_close_year_goes_on_and_relays_the_estimates_warning(self):
        """TJ-1434: the estimate says its totals leave the sale out; the lock
        takes them, and close-year dropped the warning."""
        r = self.ok(_cli(self.y, "close-year", "--yes", width="120"))
        self.assertEqual(
            self.lock()["carryforwards"]["net_capital_loss"]["closing"], 100.0)
        self.assertIn("unknown cost", " ".join(r.stderr.split()))


class TestTheCarryoverStep(unittest.TestCase):
    """TJ-1446: `d_carryover` read only `claimed_ignored`."""

    def _step(self, doc, claimed=True):
        from taxjson.lib import checklist as CL

        class Ctx:
            year, root = 2023, Path(".")
            cfg = {"carryover": {"claimed": {"2022": 40.0}}} if claimed else {}
            settings = Settings.from_table({"country": "canada"})

            def sub(self, *_a):
                return 0, json.dumps(doc), ""
        return CL.d_carryover(Ctx())

    def test_a_missing_book_needs_attention(self):
        res = self._step({"rows": [], "missing_books": ["b"]})
        self.assertEqual(res.status, "attention")
        self.assertIn("b", res.detail)
        self.assertIn("tjs run", res.detail)

    def test_an_incomplete_year_with_a_claim_needs_attention(self):
        res = self._step({"rows": [{"year": 2023, "net_gain": -100.0}],
                          "incomplete_years": [2022]})
        self.assertEqual(res.status, "attention")
        self.assertIn("2022", res.detail)

    def test_an_incomplete_year_with_no_claim_is_said_not_asked(self):
        """Attention stops a plain close-year: not for a settled unknown cost
        no figure of the lock rests on."""
        res = self._step({"rows": [], "incomplete_years": [2022]}, claimed=False)
        self.assertEqual(res.status, "manual")
        self.assertIn("2022", res.detail)

    def test_a_later_year_moves_nothing_here(self):
        res = self._step({"rows": [], "incomplete_years": [2024]})
        self.assertEqual(res.status, "manual")
        self.assertNotIn("2024", res.detail)


_FILED_LOSS = ("symbol,date,qty,proceeds,cost,gain\n"
               "ZZQF,2023-06-02,10,900,1000,-100\n")
_FILED_GAIN = ("symbol,date,qty,proceeds,cost,gain\n"
               "ZZQB.TO,2023-08-01,50,600,500,100\n")
_HOLD_2023 = "BUYSELL 2023-02-01 09:30:00 ZZQB.TO 50 CAD 10.00 -500.00 0.00\n"


class _Filed(_Project):
    def close_filed(self, text, *args, **kw):
        csv = self.top / "filed.csv"
        csv.write_text(text)
        return _cli(self.y, "close-year", "--yes", *args,
                    "--filed-dispositions", csv, **kw)


class TestAFiledLossTheBooksDoNotHave(_Filed):
    """TJ-1452: the ledger folded on the books, so a loss only the filed return
    has found no carry-back and the lock carried all of it forward: the double
    claim TJ-1422 set out to stop. No books' loss at all."""
    TRADES = {"margin": _GAIN_2022 + _HOLD_2023}

    @rule(R.CA_CARRY_01)
    def test_the_lock_splits_the_filed_loss(self):
        self.ok(self.close_filed(_FILED_LOSS, "--force"))
        ncl = self.lock()["carryforwards"]["net_capital_loss"]
        self.assertEqual((ncl["created"], ncl["carried_back"], ncl["closing"]),
                         (100.0, 40.0, 60.0))


class TestAFiledLossOverABooksGain(_Filed):
    """TJ-1452: the books have a row for the year, a gain, so no guard on a
    missing row could see this one."""
    TRADES = {"margin": _GAIN_2022 + _HOLD_2023
              + "BUYSELL 2023-08-01 09:30:00 ZZQB.TO -50 CAD 11.00 550.00 0.00\n"}

    @rule(R.CA_CARRY_01)
    def test_the_lock_splits_the_filed_loss(self):
        self.ok(self.close_filed(_FILED_LOSS))
        ncl = self.lock()["carryforwards"]["net_capital_loss"]
        self.assertEqual((ncl["carried_back"], ncl["closing"]), (40.0, 60.0))

    @rule(R.CA_CARRY_01)
    def test_a_failing_ledger_still_stops_it(self):
        r = self.close_filed(_FILED_LOSS, broken=self.broken)
        self.assertEqual(r.returncode, 1, r.stderr[-2000:])
        self.assertFalse(self.lock_path().exists())


class TestAFiledLossAboveASmallerBooksLoss(_Filed):
    """TJ-1452: the books' 10.00 capped the carry-back at 10.00 of the 40.00."""
    TOML = _TOML + _CLAIM
    TRADES = {"margin": _GAIN_2022 + _HOLD_2023
              + "BUYSELL 2023-08-01 09:30:00 ZZQB.TO -50 CAD 9.80 490.00 0.00\n"}

    @rule(R.CA_CARRY_01)
    def test_the_whole_claim_comes_out(self):
        self.ok(self.close_filed(_FILED_LOSS))
        ncl = self.lock()["carryforwards"]["net_capital_loss"]
        self.assertEqual((ncl["carried_back"], ncl["closing"]), (40.0, 60.0))


class TestAFiledSplitThatReadersAgreeOn(unittest.TestCase):
    """TJ-1473: the lock folded on the filed gain, its readers on the books, so
    a correct lock read as a claim mismatch."""

    @rule(R.CA_CARRY_01)
    def test_handoff_and_carryover_read_what_the_lock_records(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            (top / "inputs" / "margin").mkdir(parents=True)
            (top / "inputs" / "margin" / "t.tt").write_text(
                "BUYSELL 2023-02-01 09:30:00 ZZQA.TO 50 CAD 10.00 -500.00 0.00\n"
                "BUYSELL 2023-08-01 09:30:00 ZZQA.TO -50 CAD 12.00 600.00 0.00\n"
                "BUYSELL 2024-02-01 09:30:00 ZZQB.TO 50 CAD 10.00 -500.00 0.00\n")
            y23, y24 = top / "2023", top / "2024"
            y23.mkdir()
            y24.mkdir()
            (y23 / "taxjson.toml").write_text(_TOML)
            for args in (("run", "--no-input"), ("close-year", "--yes", "--force")):
                r = _cli(y23, *args)
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            (y24 / "taxjson.toml").write_text(
                _TOML.replace("year = 2023", "year = 2024").replace(
                    "[accounts.margin]",
                    'prior_year_record = "../2023/filed/2023.json"\n\n'
                    "[accounts.margin]")
                + "\n[carryover]\nclaimed = { 2023 = 40.00 }\n")
            r = _cli(y24, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            csv = top / "filed.csv"
            csv.write_text("symbol,date,qty,proceeds,cost,gain\n"
                           "ZZQF,2024-06-02,10,900,1000,-100\n")
            r = _cli(y24, "close-year", "--yes", "--force",
                     "--filed-dispositions", csv)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _cli(y24, "handoff", "--prior", y23 / "filed" / "2023.json")
            self.assertEqual(r.returncode, 0, r.stdout[-2000:] + r.stderr[-1000:])
            doc = json.loads(_cli(y24, "carryover", "--json").stdout)
            row = next(x for x in doc["rows"] if x["year"] == 2024)
            self.assertEqual((row["net_gain"], row["carried_back"],
                              row["carryforward_balance"]), (-100.0, 40.0, 60.0))
            self.assertNotIn("unmatched_claims", doc)
            # Closed again, the figures stay: the second fold reads the same gain.
            r = _cli(y24, "close-year", "--yes", "--force")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            ncl = json.loads((y24 / "filed" / "2024.json").read_text())[
                "carryforwards"]["net_capital_loss"]
            self.assertEqual((ncl["carried_back"], ncl["closing"]), (40.0, 60.0))


class TestAFiledReturnReplaced(_Filed):
    """TJ-1473's other side: `tjs carryover` reads the lock's filed gain, so a
    re-close with another return read the one it was replacing: 30.00 of the
    40.00 claim, from the old return's 30.00 loss. The books' loss is 200.00."""
    TRADES = {"margin": _GAIN_2022 + _HOLD_2023
              + "BUYSELL 2023-08-01 09:30:00 ZZQB.TO -50 CAD 6.00 300.00 0.00\n"}

    @staticmethod
    def _csv(gain):
        return ("symbol,date,qty,proceeds,cost,gain\n"
                f"ZZQF,2023-06-02,10,{1000 + gain},1000,{gain}\n")

    @rule(R.CA_CARRY_01)
    def test_the_split_is_the_new_returns(self):
        self.ok(self.close_filed(self._csv(-30)))
        ncl = self.lock()["carryforwards"]["net_capital_loss"]
        self.assertEqual((ncl["carried_back"], ncl["closing"]), (30.0, 0.0))
        for gain, closing in ((-50, 10.0), (-200, 160.0), (-300, 260.0)):
            with self.subTest(gain=gain):
                self.ok(self.close_filed(self._csv(gain), "--force"))
                ncl = self.lock()["carryforwards"]["net_capital_loss"]
                self.assertEqual((ncl["carried_back"], ncl["closing"]),
                                 (40.0, closing))
                # Reset for the next: each case replaces the -30.00 return.
                self.ok(self.close_filed(self._csv(-30), "--force"))


class TestAFiledNetHoldsTheYearsOwnSales(_Filed):
    """TJ-1474: the filed figure already has the sale, so its cost moves
    nothing: no stop, and no mark asking for a re-close."""
    TRADES = {"margin": _GAIN_2022 + _TRADES + _UNKNOWN_SALE % "2023-03-01"}

    @rule(R.CA_CARRY_03)
    def test_no_stop_and_no_mark(self):
        self.ok(self.close_filed(_FILED_LOSS))
        ncl = self.lock()["carryforwards"]["net_capital_loss"]
        self.assertEqual((ncl["carried_back"], ncl["closing"]), (40.0, 60.0))
        self.assertNotIn("carry_back_incomplete", ncl)


class TestAFiledLockAndTheYearsOwnSale(_Filed):
    """TJ-1490: the readers after the close, under the rule the close used. The
    return holds the sale, so nothing sends the person to find its cost."""
    TRADES = {"margin": _GAIN_2022 + _TRADES + _UNKNOWN_SALE % "2023-03-01"}

    def setUp(self):
        super().setUp()
        self.ok(self.close_filed(_FILED_LOSS))

    @rule(R.CA_CARRY_03)
    def test_the_estimate_warns_no_more(self):
        r = self.ok(_cli(self.y, "estimate", width="120"))
        self.assertNotIn("may change", r.stdout + r.stderr)

    @rule(R.CA_CARRY_03)
    def test_its_json_keeps_the_books_rest_for_a_plain_close(self):
        self.assertIn("carry_back_incomplete", self.estimate())

    @rule(R.CA_CARRY_03)
    def test_the_ledger_rule_spares_the_years_own_sale(self):
        from taxjson.lib.carry_back import split_left_out
        doc = json.loads(self.ok(_cli(self.y, "carryover", "--json")).stdout)
        self.assertIsNone(split_left_out(doc, 2023))

    @rule(R.CA_CARRY_03)
    def test_the_checklist_asks_nothing(self):
        r = _cli(self.y, "checklist", "--run", width="120")
        self.assertNotIn("[carryover] claimed folds", " ".join(r.stdout.split()))


class TestAFiledLockInTheEstimate(_Filed):
    """TJ-1489: it printed 70.00 carried forward, on no return: the books' loss
    less a carry-back folded on the filed one."""
    _FILED = ("symbol,date,qty,proceeds,cost,gain\n"
              "ZZQF,2023-06-02,10,470,500,-30\n")

    def setUp(self):
        super().setUp()
        self.ok(self.close_filed(self._FILED, "--force"))

    @rule(R.CA_CARRY_01)
    def test_the_books_split_and_the_locks_beside_it(self):
        est = self.estimate()
        self.assertEqual((est["losses_carried_back"],
                          est["losses_carried_forward"]), (40.0, 60.0))
        self.assertEqual(est["locked_split"],
                         {"carried_back": 30.0, "carried_forward": 0.0})

    @rule(R.CA_CARRY_01)
    def test_the_text_says_both(self):
        text = self.ok(_cli(self.y, "estimate", width="120")).stdout
        self.assertIn("Unused capital losses:  60.00", text)
        self.assertRegex(text, r"Locked for 2023:\s+0\.00\s+\(carry forward; "
                               r"30\.00 carried back")

    @rule(R.CA_CARRY_01)
    def test_the_ledgers_details_name_the_return(self):
        """TJ-1483: called it the RETURN row of `tjs sum`."""
        text = " ".join(self.ok(_cli(self.y, "carryover", "--details")).stdout.split())
        self.assertIn("has the return filed with another tool", text)
        self.assertNotIn("2023's NET GAIN(LOSS) has the RETURN row", text)


class TestAFiledNetOverAnEarlierYearsSale(_Filed):
    """TJ-1474's other side: an earlier year's result can still move the fold."""
    TRADES = {"margin": _GAIN_2022 + _TRADES + _UNKNOWN_SALE % "2021-03-01"}

    @rule(R.CA_CARRY_03)
    def test_it_still_stops(self):
        r = self.close_filed(_FILED_LOSS)
        self.assertEqual(r.returncode, 1, r.stderr[-2000:])
        self.assertIn("(2021)", " ".join(r.stderr.split()))
        self.assertFalse(self.lock_path().exists())

    @rule(R.CA_CARRY_03)
    def test_a_year_filter_leaves_an_undated_sale(self):
        from taxjson.lib.carry_back import filed_left_out
        self.assertIsNone(filed_left_out(None, 2023))
        self.assertIsNone(filed_left_out({"years": [2023], "sales": []}, 2023))
        left = filed_left_out({"years": [2023], "undated": True, "sales": [
            {"year": 2023}, {"year": None, "symbol": "Z"}]}, 2023)
        self.assertEqual(left["years"], [])
        self.assertEqual([s.get("symbol") for s in left["sales"]], ["Z"])


class TestAFiledGainOverABooksLoss(_Filed):
    """TJ-1456: the return shows no loss, so there is nothing to split."""
    TRADES = {"margin": _GAIN_2022 + _TRADES + _UNKNOWN_SALE % "2021-03-01"}

    @rule(R.CA_CARRY_03)
    def test_no_stop_and_nothing_carried_back(self):
        self.ok(self.close_filed(_FILED_GAIN))
        ncl = self.lock()["carryforwards"]["net_capital_loss"]
        self.assertEqual(ncl["closing"], 0.0)
        self.assertNotIn("carried_back", ncl)
        self.assertNotIn("carry_back_incomplete", ncl)

    @rule(R.CA_CARRY_03)
    def test_a_failing_ledger_does_not_stop_it(self):
        self.ok(self.close_filed(_FILED_GAIN, broken=self.broken))
        self.assertEqual(
            self.lock()["carryforwards"]["net_capital_loss"]["closing"], 0.0)


class TestABalanceCarriedIntoAGainYear(_Project):
    """TJ-1454: a carried-in balance is no loss of this year, so the sale left
    out moves no split here."""
    YEAR = 2024
    TOML = (_TOML.replace("year = 2023", "year = 2024")
            + "other_losses = 900\n\n[carryover]\nclaimed = { 2023 = 500.00 }\n")
    TRADES = {"margin": _UNKNOWN_SALE % "2021-03-01"
              + "BUYSELL 2024-02-01 09:30:00 ZZQG.TO 50 CAD 10.00 -500.00 0.00\n"
              "BUYSELL 2024-08-01 09:30:00 ZZQG.TO -50 CAD 12.00 600.00 0.00\n"}

    @rule(R.CA_CARRY_03)
    def test_no_stop_and_no_mark(self):
        est = self.estimate()
        self.assertEqual(est["losses_carried_back"], 0.0)
        self.assertNotIn("carry_back_incomplete", est)
        self.ok(_cli(self.y, "close-year", "--yes"))
        self.assertNotIn("carry_back_incomplete",
                         self.lock()["carryforwards"]["net_capital_loss"])


class TestAGainYearCanCarryBack(unittest.TestCase):
    """Why TJ-1454 was not fixed by reading `losses_created` as its report
    proposed: a lock that recorded no carry-back leaves the claim to be met from
    the balance it carried, in a year with no loss."""

    @rule(R.CA_CARRY_03)
    def test_the_figure_is_kept(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            (top / "inputs" / "margin").mkdir(parents=True)
            (top / "inputs" / "margin" / "t.tt").write_text(_GAIN_2022 + _TRADES)
            y = top / "proj"
            y.mkdir()
            (y / "taxjson.toml").write_text(_TOML)
            for args in (("run", "--no-input"), ("close-year", "--yes")):
                r = _cli(y, *args)
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            (top / "inputs" / "margin" / "u.tt").write_text(
                "BUYSELL 2024-02-01 09:30:00 ZZQG.TO 10 CAD 10.00 -100.00 0.00\n"
                "BUYSELL 2024-08-01 09:30:00 ZZQG.TO -10 CAD 11.00 110.00 0.00\n")
            (y / "taxjson.toml").write_text(
                _TOML.replace("year = 2023", "year = 2024") + _CLAIM)
            r = _cli(y, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _cli(y, "estimate", "--json")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            est = json.loads(r.stdout)["estimate"]
        self.assertEqual(est["losses_created"], 0.0)
        self.assertEqual(est["losses_carried_back"], 40.0)


class TestASaleBeforeALockedYear(unittest.TestCase):
    """TJ-1455: the balance restarts from the lock, so the sale can no longer
    move anything: no stop, however many years later."""

    @rule(R.CA_CARRY_03)
    def test_no_stop_and_no_caveat(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            (top / "inputs" / "margin").mkdir(parents=True)
            (top / "inputs" / "margin" / "t.tt").write_text(
                _UNKNOWN_SALE % "2021-03-01" + _GAIN_2022 + _TRADES)
            y22, y23 = top / "2022", top / "2023"
            y22.mkdir()
            y23.mkdir()
            (y22 / "taxjson.toml").write_text(
                _TOML.replace("year = 2023", "year = 2022"))
            for args in (("run", "--no-input"), ("close-year", "--yes")):
                r = _cli(y22, *args)
                self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            (y23 / "taxjson.toml").write_text(
                _TOML.replace('[accounts.margin]',
                              'prior_year_record = "../2022/filed/2022.json"'
                              '\n\n[accounts.margin]') + _CLAIM)
            r = _cli(y23, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            r = _cli(y23, "carryover", "--json")
            self.assertEqual(json.loads(r.stdout)["incomplete_years"], [2021])
            r = _cli(y23, "estimate", "--json")
            est = json.loads(r.stdout)["estimate"]
            self.assertEqual(est["losses_carried_back"], 40.0)
            self.assertNotIn("carry_back_incomplete", est)
            r = _cli(y23, "close-year", "--yes")
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            lock = json.loads((y23 / "filed" / "2023.json").read_text())
        ncl = lock["carryforwards"]["net_capital_loss"]
        self.assertEqual((ncl["carried_back"], ncl["closing"]), (40.0, 60.0))
        self.assertNotIn("carry_back_incomplete", ncl)


class TestTheRule(unittest.TestCase):
    """lib/carry_back.py: the one rule the stop, the estimate, handoff and the
    checklist read (TJ-1453, TJ-1455, TJ-1469)."""
    LOSS = {"year": 2023, "net_gain": -100.0, "carried_back": 40.0}

    def left(self, doc, year=2023):
        from taxjson.lib.carry_back import ledger_left_out
        return ledger_left_out(doc, year)

    def split(self, doc, year=2023):
        from taxjson.lib.carry_back import split_left_out
        return split_left_out(doc, year)

    def test_a_year_up_to_this_one_counts(self):
        self.assertEqual(self.left({"rows": [self.LOSS],
                                    "incomplete_years": [2021]})["years"], [2021])

    def test_a_later_year_does_not(self):
        self.assertIsNone(self.left({"rows": [], "incomplete_years": [2024]}))

    def test_the_rows_flag_counts(self):
        self.assertEqual(self.left({"rows": [{"year": 2022, "incomplete": True}]}
                                   )["years"], [2022])

    def test_a_lock_clears_what_is_at_or_before_it(self):
        rows = [{"year": 2022, "balance_from_lock": True}, self.LOSS]
        self.assertIsNone(self.left({"rows": rows,
                                     "incomplete_years": [2021, 2022]}))
        self.assertEqual(self.left({"rows": rows, "incomplete_years":
                                    [2022, 2023]})["years"], [2023])

    def test_the_projects_own_lock_clears_nothing(self):
        """A re-close replaces it."""
        rows = [dict(self.LOSS, balance_from_lock=True)]
        self.assertEqual(self.left({"rows": rows,
                                    "incomplete_years": [2023]})["years"], [2023])

    def test_an_undated_sale_counts_even_past_a_lock(self):
        """TJ-1453: no year filter can clear it."""
        rows = [{"year": 2022, "balance_from_lock": True}, self.LOSS]
        left = self.left({"rows": rows, "incomplete_years": [],
                          "excluded_unknown_cost": [{"year": None, "symbol": "Z"}]})
        self.assertEqual((left["years"], left["undated"]), ([], True))
        self.assertEqual([s["symbol"] for s in left["sales"]], ["Z"])

    def test_a_split_needs_a_loss_or_a_carry_back(self):
        doc = {"incomplete_years": [2021]}
        self.assertIsNone(self.split(dict(doc, rows=[])))
        self.assertIsNone(self.split(dict(doc, rows=[
            {"year": 2023, "net_gain": 10.0, "carried_back": 0.0}])))
        self.assertIsNotNone(self.split(dict(doc, rows=[
            {"year": 2023, "net_gain": -10.0, "carried_back": 0.0}])))
        self.assertIsNotNone(self.split(dict(doc, rows=[
            {"year": 2023, "net_gain": 10.0, "carried_back": 5.0}])))

    def test_a_damaged_document_is_no_traceback(self):
        for doc in ({"rows": 3}, {"rows": [3, {"year": "x"}]},
                    {"rows": [], "incomplete_years": "x"},
                    {"rows": [{"year": 2023, "net_gain": "x"}],
                     "incomplete_years": [2021, "x"]},
                    {"rows": [], "excluded_unknown_cost": "x"}):
            with self.subTest(doc=doc):
                self.split(doc)

    def test_the_text_stays_short(self):
        from taxjson.lib.carry_back import left_out_text
        self.assertEqual(left_out_text({"years": [2021]}), "2021")
        self.assertEqual(left_out_text({"years": list(range(2010, 2018))}),
                         "2010, 2011, 2012, 2013, 2014, and 3 more")
        self.assertEqual(left_out_text({"years": [2021], "undated": True}),
                         "2021, undated")


class TestAFigureTooBigForAFloat(_Project):
    """TJ-1458: OverflowError instead of a refusal."""
    HUGE = "1" + "0" * 400

    def test_finite_num_says_no(self):
        from taxjson.lib.tx_load import finite_num
        self.assertFalse(finite_num(int(self.HUGE)))
        self.assertTrue(finite_num(1000))
        self.assertFalse(finite_num(float("nan")))
        self.assertFalse(finite_num(True))

    @rule(R.CA_CARRY_03)
    def test_the_ledger_is_refused(self):
        for name, row in (
                ("back", '{"year":2023,"carried_back":%s}' % self.HUGE),
                ("to", '{"year":2023,"carried_back":40,"carried_back_to":'
                       '{"2022":%s}}' % self.HUGE)):
            engine = self.engine(f"huge{name}",
                                 _FAKE_LEDGER % ('{"rows":[%s]}' % row,))
            with self.subTest(row=name):
                r = _cli(self.y, "close-year", "--yes", broken=engine)
                self.assertEqual(r.returncode, 1, r.stderr[-2000:])
                self.assertIn("the carryover ledger could not be used", r.stderr)
                self.assertNotIn("OverflowError", r.stderr)


class TestLeftOutSalesThatAreNotSales(_Project):
    """TJ-1459: `excluded_unknown_cost` was outside the validating reader."""

    @rule(R.CA_CARRY_03)
    def test_the_ledger_is_refused(self):
        for name, excl in (("proceeds", '[{"year":2021,"proceeds":"n/a"}]'),
                           ("year", '[{"year":"x","proceeds":1}]'),
                           ("entry", "[3]"), ("list", '"x"')):
            engine = self.engine(f"excl{name}", _FAKE_LEDGER % (
                '{"rows":[{"year":2023,"carried_back":40,"net_gain":-100}],'
                '"incomplete_years":[2021],"excluded_unknown_cost":%s}' % excl,))
            with self.subTest(excl=name):
                r = _cli(self.y, "close-year", "--yes", broken=engine)
                self.assertEqual(r.returncode, 1, r.stderr[-2000:])
                self.assertIn("excluded_unknown_cost", r.stderr)
                self.assertNotIn("Traceback", r.stderr)
                self.assertFalse(self.lock_path().exists())

    def test_an_estimates_mark_of_another_shape_stops(self):
        from unittest import mock
        from taxjson.cli import file as F
        for mark in ("x", {"years": "x"}, []):
            with self.subTest(mark=mark), self.assertRaises(SystemExit), \
                    mock.patch.object(F, "_claims_back", return_value=False):
                F._split_for_lock(Path("."), {}, Settings.from_table({"year": 2023}),
                                  {"losses_carried_back": 40.0,
                                   "carry_back_incomplete": mark},
                                  {"closing": 100.0}, None)


class TestMissingBooksThatAreNotAList(_Project):
    """TJ-1492: a traceback, where every other bad field is a named stop."""

    @rule(R.CA_CARRY_03)
    def test_a_named_stop(self):
        r = _cli(self.y, "close-year", "--yes", broken=self.fake(json.dumps(
            {"rows": [], "missing_books": 5})))
        self.assertEqual(r.returncode, 1, r.stderr[-800:])
        self.assertNotIn("Traceback", r.stderr)
        self.assertIn("missing_books", r.stderr)

class TestAnAcceptedSplit(_Project):
    """TJ-1460, TJ-1461, TJ-1463, TJ-1464, TJ-1465, the owner's decisions."""
    TRADES = {"margin": _GAIN_2022 + _TRADES + _UNKNOWN_SALE % "2021-03-01"}

    def _terminal(self, answer):
        """A terminal for close-year itself, not for the children it runs."""
        return self.engine(f"tty{answer}", (
            "import builtins, io, sys\n"
            "if 'close-year' in sys.orig_argv:\n"
            "    class _T(io.StringIO):\n"
            "        def isatty(self):\n"
            "            return True\n"
            f"    sys.stdin = _T({answer + chr(10)!r})\n"))

    @rule(R.CA_CARRY_03)
    def test_the_stop_does_not_name_a_figure_the_lock_may_lack(self):
        r = _cli(self.y, "close-year", "--yes", width="120")
        self.assertEqual(r.returncode, 1)
        text = " ".join(r.stderr.split())
        self.assertIn("the split of this year's loss leaves out sales", text)
        self.assertNotIn("the carry-back this lock records", text)

    @rule(R.CA_CARRY_03)
    def test_force_names_the_sales_in_the_default_view(self):
        r = self.ok(_cli(self.y, "close-year", "--yes", "--force", width="120"))
        text = " ".join(r.stderr.split())
        self.assertIn("(--force)", text)
        self.assertIn("ZZQE.TO", text)

    @rule(R.CA_CARRY_03)
    def test_a_yes_covers_a_split_the_question_did_not_list(self):
        """TJ-1491: a filed close leaves the split out of the question, and
        stopped after the yes, asking for --force."""
        csv = self.top / "filed.csv"
        csv.write_text(_FILED_LOSS)
        r = _cli(self.y, "close-year", "--filed-dispositions", csv,
                 broken=self._terminal("y"), width="120")
        self.assertIn("Lock 2023 anyway?", r.stdout + r.stderr)
        self.ok(r)
        self.assertIn("(answered yes)", " ".join(r.stderr.split()))
        ncl = self.lock()["carryforwards"]["net_capital_loss"]
        self.assertEqual(ncl["carry_back_incomplete"], _LOCKED_2021)

    @rule(R.CA_CARRY_03)
    def test_force_on_a_pipe_does_not_ask_for_yes_about_the_split(self):
        """TJ-1475: --force asked for --yes, and --yes for --force."""
        r = _cli(self.y, "close-year", "--force")
        self.assertNotIn("carryover:", r.stderr)

    @rule(R.CA_CARRY_03)
    def test_the_estimates_warning_is_not_said_again(self):
        r = self.ok(_cli(self.y, "close-year", "--yes", "--force", "--details"))
        self.assertNotIn("may change (`tjs carryover`)", r.stderr)
        self.assertNotIn("`tjs close-year` stops on this", r.stderr)

    @rule(R.CA_CARRY_03)
    def test_the_lock_records_it(self):
        self.ok(_cli(self.y, "close-year", "--yes", "--force"))
        ncl = self.lock()["carryforwards"]["net_capital_loss"]
        self.assertEqual(ncl["carry_back_incomplete"], _LOCKED_2021)
        self.assertEqual((ncl["carried_back"], ncl["closing"]), (40.0, 60.0))

    @rule(R.CA_CARRY_03)
    def test_the_next_handoff_says_so(self):
        from taxjson.lib.carryforward import handoff_issues
        self.ok(_cli(self.y, "close-year", "--yes", "--force"))
        record = self.lock()
        res = handoff_issues(self.y, {"settings": Settings.from_table({"country": "canada",
                                                   "year": 2024})}, record)
        self.assertIn("2021", res.split_note)
        self.assertIn("re-close 2023 with --force", res.split_note)
        self.assertEqual(res.issues, [])

    def test_a_record_without_the_mark_says_nothing(self):
        from taxjson.lib.carryforward import handoff_issues
        res = handoff_issues(
            self.y, {"settings": Settings.from_table({"country": "canada", "year": 2024})},
            {"year": 2023, "carryforwards": {"net_capital_loss": {"closing": 60.0}}})
        self.assertIsNone(res.split_note)

    @rule(R.CA_CARRY_03)
    def test_yes_on_a_terminal_accepts_it(self):
        """One decision: the person answered with the item listed."""
        r = _cli(self.y, "close-year", broken=self._terminal("y"), width="120")
        self.ok(r)
        self.assertIn("answered yes", " ".join(r.stderr.split()))
        self.assertEqual(self.lock()["carryforwards"]["net_capital_loss"]
                         ["carry_back_incomplete"], _LOCKED_2021)

    @rule(R.CA_CARRY_03)
    def test_no_on_a_terminal_locks_nothing(self):
        r = _cli(self.y, "close-year", broken=self._terminal("n"))
        self.assertEqual(r.returncode, 1)
        self.assertFalse(self.lock_path().exists())

    @rule(R.CA_CARRY_03)
    def test_settled_and_closed_again_the_mark_goes(self):
        self.ok(_cli(self.y, "close-year", "--yes", "--force"))
        (self.y / "inputs" / "margin" / "trades.tt").write_text(
            _GAIN_2022 + _TRADES)
        self.ok(_cli(self.y, "run", "--no-input"))
        self.ok(_cli(self.y, "close-year", "--yes", "--force"))
        self.assertNotIn("carry_back_incomplete",
                         self.lock()["carryforwards"]["net_capital_loss"])


class TestTheLinesFitTheWidth(_Project):
    """TJ-1466: a pipe's 120 columns."""
    TRADES = {"margin": _GAIN_2022 + _TRADES + "".join(
        _UNKNOWN_SALE.replace("ZZQE", f"ZZQ{c}").replace("2020-12-31", "2012-12-31")
        % f"{y}-03-01" for c, y in zip("CDEFGHIJ", range(2013, 2021)))}

    def _lines(self, **kw):
        r = _cli(self.y, "estimate", width="120", **kw)
        return [ln for ln in r.stdout.splitlines()], r

    @rule(R.CA_CARRY_03)
    def test_with_sales_left_out(self):
        lines, r = self._lines()
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        at = next(i for i, ln in enumerate(lines)
                  if "Unused capital losses" in ln)
        self.assertIn("carried back", lines[at])
        self.assertIn("may change", lines[at + 1])
        self.assertIn("and 3 more", lines[at + 1])
        for ln in lines[at:at + 2]:
            self.assertLessEqual(len(ln), 120, ln)

    @rule(R.CA_CARRY_03)
    def test_with_a_ledger_that_fails(self):
        lines, r = self._lines(broken=self.broken)
        at = next(i for i, ln in enumerate(lines)
                  if "Unused capital losses" in ln)
        self.assertIn("unknown", lines[at])
        self.assertIn("could not be used", lines[at + 1])
        for ln in lines[at:at + 2]:
            self.assertLessEqual(len(ln), 120, ln)


class TestOneBuildForALedgerThatFailed(unittest.TestCase):
    """TJ-1472: the estimate's reason is the close's."""

    def test_the_reason_stops_it_without_another_build(self):
        from unittest import mock
        from taxjson.cli import file as F
        with mock.patch.object(F, "_carry_back") as cb, \
                self.assertRaises(SystemExit) as cm:
            F._split_for_lock(Path("."), {}, Settings.from_table({"year": 2023}),
                              {"carry_back_unknown": "the synthetic reason"},
                              {"closing": 100.0}, None)
        cb.assert_not_called()
        self.assertIn("the synthetic reason", str(cm.exception.code))


class TestTheRelay(unittest.TestCase):
    """TJ-1465: what the caller has dealt with is left out whole."""

    def test_a_handled_message_goes_with_its_details(self):
        import contextlib
        import io
        from taxjson.cli.common import _relay_child_stderr
        from taxjson.pipeline import run_state
        err = io.StringIO()
        text = ("tjs estimate: warning: the handled one\n  its detail\n"
                "tjs estimate: warning: another\n  its own detail\n")
        old = run_state.current_details
        run_state.current_details = True
        self.addCleanup(setattr, run_state, "current_details", old)
        with contextlib.redirect_stderr(err):
            _relay_child_stderr(text, handled=("the handled one",))
        out = err.getvalue()
        self.assertNotIn("handled one", out)
        self.assertNotIn("its detail", out)
        self.assertIn("another", out)
        self.assertIn("its own detail", out)


class TestTheCarryoverStepAsksOnlyForASplit(unittest.TestCase):
    """TJ-1457: attention stops a piped close-year, every year."""
    DOC = {"rows": [{"year": 2023, "net_gain": -100.0, "carried_back": 40.0}],
           "incomplete_years": [2021]}

    def _step(self, claimed, doc=None, country="canada"):
        from taxjson.lib import checklist as CL

        class Ctx:
            year, root = 2023, Path(".")
            cfg = {"carryover": {"claimed": claimed}}
            settings = Settings.from_table({"country": country})

            def sub(self, *_a):
                return 0, json.dumps(doc or TestTheCarryoverStepAsksOnlyForASplit.DOC), ""
        return CL.d_carryover(Ctx())

    def test_missing_books_that_are_not_names(self):
        """TJ-1492: `join` raised on them."""
        for books, named in ((5, "5"), ([1], "1"), (["margin"], "margin")):
            res = self._step({}, {"rows": [], "missing_books": books})
            self.assertEqual(res.status, "attention")
            self.assertIn(f"no base file for {named}:", res.detail)

    def test_a_claim_in_the_window_on_a_loss_asks(self):
        res = self._step({"2022": 40.0})
        self.assertEqual(res.status, "attention")
        from taxjson.lib.carry_back import SPLIT_ATTENTION
        self.assertIn(SPLIT_ATTENTION, res.detail)

    def test_no_split_at_stake_does_not_ask(self):
        gain = {"rows": [{"year": 2023, "net_gain": 10.0, "carried_back": 0.0}],
                "incomplete_years": [2021]}
        for name, claimed, doc, country in (
                ("us", {"2022": 40.0}, None, "usa"),
                ("project year", {"2023": 40.0}, None, "canada"),
                ("past the window", {"2019": 40.0}, None, "canada"),
                ("zero", {"2022": 0}, None, "canada"),
                ("no loss", {"2022": 40.0}, gain, "canada")):
            with self.subTest(case=name):
                res = self._step(claimed, doc, country)
                self.assertEqual(res.status, "manual", res.detail)
                self.assertIn("2021", res.detail)

    def test_a_project_with_no_year_is_no_traceback(self):
        from taxjson.lib import checklist as CL

        class Ctx:
            year, root = None, Path(".")
            cfg = {"carryover": {"claimed": {"2022": 40.0}}}
            settings = Settings.from_table({"country": "canada"})

            def sub(self, *_a):
                return 0, json.dumps(TestTheCarryoverStepAsksOnlyForASplit.DOC), ""
        self.assertEqual(CL.d_carryover(Ctx()).status, "manual")

    def test_a_sale_before_a_locked_year_is_not_even_said(self):
        doc = {"rows": [{"year": 2022, "balance_from_lock": True},
                        {"year": 2023, "net_gain": -100.0, "carried_back": 40.0}],
               "incomplete_years": [2021]}
        res = self._step({"2022": 40.0}, doc)
        self.assertEqual(res.status, "manual")
        self.assertNotIn("2021", res.detail)


if __name__ == "__main__":
    unittest.main()
