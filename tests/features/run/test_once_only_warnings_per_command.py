"""TJ-1412, TJ-1507, TJ-1510: a once-only warning is once per command, and a new
run says its warnings again.

A nested command's stderr can be dropped, so what it says can't count as said for
its caller. Synthetic data only: made-up tickers, amounts under a thousand.

Formerly: test_fix_tj1412_state
"""
import ast
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _support.ca_t1a_year import _Year
from _support.years_carryover_ledger import _cli

from taxjson.lib import once, report_model
from taxjson.pipeline import run_state
import _paths

SRC = _paths.REPO / "src" / "taxjson"
STALE = "was built before"
_LATER_LOSS = ("BUYSELL 2024-02-01 09:30:00 ZZQC.TO 10 CAD 10.00 -100.00 0.00\n"
               "BUYSELL 2024-03-01 09:30:00 ZZQC.TO -10 CAD 8.00 80.00 0.00\n"
               "BUYSELL 2024-03-11 09:30:00 ZZQC.TO 10 CAD 8.00 -80.00 0.00\n"
               'ALLOWLOSS 2024-03-01 ZZQC.TO reason="synthetic"\n')


def _stale(project):
    base = project / "work" / "margin_base.json"
    base.write_text(base.read_text() + "\n")


class TestANestedCommandsWarningIsNotTheCallers(unittest.TestCase):
    """The ledger's `tjs carryover --json` read the stale books first and its
    stderr is dropped: `handoff` then said nothing about them."""

    def test_handoff_still_warns_about_stale_books(self):
        with tempfile.TemporaryDirectory() as t:
            year = _Year(Path(t), -100.0)
            year.close()
            later = year.next_year()
            (year.top / "inputs" / "margin" / "u.tt").write_text(_LATER_LOSS)
            year.run(later, "run", "--no-input")
            _stale(later)
            r = _cli(later, "handoff", "--prior",
                     year.y / "filed" / "2023.json")
            self.assertEqual(r.returncode, 0, r.stdout[-400:] + r.stderr[-400:])
            self.assertEqual((r.stdout + r.stderr).count(STALE), 1)

    def test_a_relayed_child_does_not_say_it_twice(self):
        with tempfile.TemporaryDirectory() as t:
            year = _Year(Path(t), -100.0)
            _stale(year.y)
            for cmd in ("estimate", "amt", "carryover"):
                r = _cli(year.y, cmd)
                self.assertEqual((r.stdout + r.stderr).count(STALE), 1, cmd)


class TestAChainSaysAWarningOnce(unittest.TestCase):
    """TJ-1516: what a caller relayed from its nested command was said."""

    def test_after_a_relay_the_next_command_does_not_repeat_it(self):
        with tempfile.TemporaryDirectory() as t:
            year = _Year(Path(t), -100.0)
            (year.y / "taxjson.toml").write_text(
                (year.y / "taxjson.toml").read_text()
                + "\n[instalments]\nzzq_unknown = 1\n")
            _stale(year.y)
            for chain in (["amt", "sum"], ["amt", "estimate"]):
                r = _cli(year.y, *chain)
                said = r.stdout + r.stderr
                # The estimate amt builds on refuses such books (TJ-4347), so
                # the chain stops there: nothing said twice.
                self.assertEqual(r.returncode, 1, chain)
                self.assertIn("could not compute the estimate", said, chain)
                self.assertLessEqual(said.count(STALE), 1, chain)
                self.assertLessEqual(said.count("zzq_unknown"), 1, chain)


class TestAChainOfProjects(unittest.TestCase):
    """TJ-1519, TJ-1521: the same words about another project's file."""

    def _projects(self, t):
        made = []
        for name in ("a", "b"):
            root = Path(t) / name
            d = root / "inputs" / "margin"
            d.mkdir(parents=True)
            (d / "t.tt").write_text(
                "BUYSELL 2023-02-01 09:30:00 ZZQA.TO 50 CAD 10.00 -500.00 0.00\n"
                "BUYSELL 2023-08-01 09:30:00 ZZQA.TO -50 CAD 12.00 600.00 0.00\n")
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2023\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                'tax_date = "settle"\nprovince = "ON"\nzzq_typo = 1\n\n'
                '[accounts.margin]\ntype = "taxable"\n')
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            _stale(root)
            made.append(root)
        return made

    def test_each_project_is_told(self):
        with tempfile.TemporaryDirectory() as t:
            a, b = self._projects(t)
            # (`tjs sum` refuses such books, TJ-4347: winners reads them.)
            r = _cli(a, "winners", "--", "-C", str(b), "winners")
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            said = r.stdout + r.stderr
            self.assertEqual(said.count(STALE), 2)
            self.assertEqual(said.count("zzq_typo"), 2)


class TestANewRunSaysItsWarningsAgain(unittest.TestCase):
    """TJ-1510: `tjs sum` after a second run in one process served the stale
    wash-adjusted figures with no warning."""

    def test_the_second_sum_of_a_chain_warns_too(self):
        toml = ('[settings]\nyear = 2023\ncountry = "canada"\n'
                'base_currency = "CAD"\nsource_currencies = []\n'
                'tax_date = "settle"\nprovince = "ON"\n\n'
                '[accounts.a]\ntype = "taxable"\n\n'
                '[accounts.b]\ntype = "taxable"\n')
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / "p"
            for acct, sym in (("a", "ZZQA.TO"), ("b", "ZZQB.TO")):
                d = root / "inputs" / acct
                d.mkdir(parents=True)
                (d / "t.tt").write_text(
                    f"BUYSELL 2023-02-01 09:30:00 {sym} 50 CAD 10.00 -500.00 0.00\n"
                    f"BUYSELL 2023-08-01 09:30:00 {sym} -50 CAD 12.00 600.00 0.00\n")
            (root / "taxjson.toml").write_text(toml)
            self.assertEqual(_cli(root, "run", "--no-input").returncode, 0)
            # a's sale edited: its books move (a rebuild of the same bytes
            # leaves the wash figures current, since the books carry no
            # merge time).
            tt = root / "inputs" / "a" / "t.tt"
            tt.write_text(tt.read_text().replace("12.00 600.00", "13.00 650.00"))
            # (`tjs sum` refuses an --account run's books, TJ-4347:
            # winners reads them.)
            one = ["run", "--account", "a", "--no-input", "winners"]
            once_over = _cli(root, *one)
            twice = _cli(root, *one, *one)
            self.assertEqual(twice.returncode, 0, twice.stderr[-400:])
            said = (once_over.stdout + once_over.stderr).count(STALE)
            self.assertGreater(said, 0)
            self.assertEqual((twice.stdout + twice.stderr).count(STALE), 2 * said)


class TestTheScopes(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(run_state, "current", run_state.RunState())
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(report_model._STALE_SAID.clear)
        report_model._STALE_SAID.clear()

    def test_a_nested_command_knows_what_its_caller_said(self):
        run_state.current.shown.add("outer")
        report_model._STALE_SAID.add("outer")
        with run_state.nested_command():
            self.assertIn("outer", run_state.current.shown)
            self.assertIn("outer", report_model._STALE_SAID)

    def test_what_it_says_is_forgotten_when_it_returns(self):
        with run_state.nested_command():
            run_state.current.shown.add("inner")
            run_state.current.config_warned.add("inner")
            report_model._STALE_SAID.add("inner")
        self.assertEqual(run_state.current.shown, set())
        self.assertEqual(run_state.current.config_warned, set())
        self.assertEqual(report_model._STALE_SAID, set())

    def test_it_comes_back_after_a_failure(self):
        outer = vars(run_state)["current"]
        with self.assertRaises(SystemExit), run_state.nested_command():
            report_model._STALE_SAID.add("inner")
            raise SystemExit(2)
        self.assertIs(vars(run_state)["current"], outer)
        self.assertEqual(report_model._STALE_SAID, set())

    def test_a_set_first_made_inside_starts_empty_after(self):
        with mock.patch.object(once, "_SETS", []), once.nested():
            late = once.said_set()
            late.add("inner")
        self.assertEqual(late, set())

    def test_a_new_run_starts_clean(self):
        run_state.current.config_warned.add("toml")
        run_state.current.missing_base_warned.add("base")
        run_state.current.listing_groups[(Path("w"), "a")] = {}
        run_state.current.echo_skip = True
        report_model._STALE_SAID.add("stale")
        run_state.begin_run()
        self.assertEqual(
            run_state.current,
            run_state.RunState(config_warned={"toml"}),
            "only taxjson.toml's warnings are kept (TJ-1507)")
        self.assertEqual(report_model._STALE_SAID, set())

    def test_what_a_caller_showed_counts_and_what_it_dropped_does_not(self):
        with run_state.nested_command():
            run_state.current.shown.add("dropped")
            report_model._STALE_SAID.add("dropped")
        with run_state.nested_command():
            run_state.current.shown.add("shown")
            report_model._STALE_SAID.add("shown")
        run_state.relayed_nested()
        self.assertEqual(run_state.current.shown, {"shown"})
        self.assertEqual(report_model._STALE_SAID, {"shown"})

    def test_a_relay_with_no_nested_command_adopts_nothing(self):
        with run_state.nested_command():
            run_state.current.shown.add("x")
        run_state.relayed_nested()
        run_state.current.shown.clear()
        report_model._STALE_SAID.clear()
        run_state.relayed_nested()
        self.assertEqual(run_state.current.shown, set())

    def test_config_warnings_start_over_for_another_project(self):
        run_state.config_warnings_for(Path("/a"))
        run_state.current.config_warned.add("w")
        run_state.config_warnings_for(Path("/a"))
        self.assertEqual(run_state.current.config_warned, {"w"})
        run_state.config_warnings_for(Path("/b"))
        self.assertEqual(run_state.current.config_warned, set())


class TestEverySaidOnceSetIsScoped(unittest.TestCase):
    """By what a set does, not its name: tested with `in` and added to."""

    @staticmethod
    def _plain(tree):
        plain = set()
        for n in tree.body:
            if not isinstance(n, (ast.Assign, ast.AnnAssign)):
                continue
            v = n.value
            if isinstance(v, ast.Set) or (
                    isinstance(v, ast.Call) and not v.args
                    and getattr(v.func, "id", None) == "set"):
                targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                plain.update(t.id for t in targets if isinstance(t, ast.Name))
        added = {c.func.value.id for c in ast.walk(tree)
                 if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                 and c.func.attr == "add" and isinstance(c.func.value, ast.Name)}
        tested = {x.id for c in ast.walk(tree) if isinstance(c, ast.Compare)
                  and any(isinstance(o, (ast.In, ast.NotIn)) for o in c.ops)
                  for x in c.comparators if isinstance(x, ast.Name)}
        return plain & added & tested

    def test_no_module_keeps_a_plain_one(self):
        bad = {f"{p.name}: {name}" for p in SRC.rglob("*.py")
               for name in self._plain(ast.parse(p.read_text("utf-8")))}
        self.assertEqual(bad, set(), "`once.said_set()`: a nested command scopes it")

    def test_the_check_goes_by_what_it_does(self):
        tree = ast.parse("_TOLD = set()\ndef f(k):\n    if k not in _TOLD:\n"
                         "        _TOLD.add(k)\n")
        self.assertEqual(self._plain(tree), {"_TOLD"})


if __name__ == "__main__":
    unittest.main()
