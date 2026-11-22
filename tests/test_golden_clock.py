"""The golden books a thousand days later, on Python 3.11's float sum(): the
same answer (tests/_golden.py).

A book's dates are all long past, and what reads the clock (a report's
generated_at, a TXF header) is masked, so a run on any later day must record
what was recorded. tests/_clockshift moves the clock of every Python process
of the run; the first tests prove it does, in a run's own stages too. The same
run adds floats with sum() as Python 3.10 and 3.11 do (tests/_oldsum): the
books are recorded on 3.12, whose sum() compensates, and a figure must not
depend on which (TJ-2791). One class per book, so the suite can run them side
by side."""
import datetime
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _golden
import _paths

DAYS = 1000
_SHIMS = _paths.TESTS
_PROBE = ("import datetime, json, pickle, sys, time\n"
          "class MyDate(datetime.date): pass\n"
          "d = datetime.datetime(2024, 1, 2)\n"
          "print(json.dumps(["
          "datetime.date.today().isoformat(), datetime.datetime.now().date().isoformat(), "
          "time.strftime('%Y-%m-%d'), int(time.time()), "
          "isinstance(d.date(), datetime.date), issubclass(datetime.datetime, datetime.date), "
          "isinstance(datetime.date.today(), datetime.date), type(d).__name__, "
          "isinstance(datetime.date(2024, 1, 1), MyDate), isinstance(MyDate(2024, 1, 1), MyDate), "
          "str(pickle.loads(pickle.dumps(d.date()))), str(pickle.loads(pickle.dumps(d))), "
          "'pandas' in sys.modules]))")


def _probe(env: dict, code: str = _PROBE) -> list:
    # An empty folder: -c reads modules from the cwd first.
    with tempfile.TemporaryDirectory() as cwd:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=cwd,
                           env=env, timeout=120, check=False)
    if r.returncode or r.stderr.strip():
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


def _later(days: int = DAYS) -> set:
    today = datetime.date.today()
    return {(today + datetime.timedelta(days=days + k)).isoformat() for k in (0, 1)}


class TestTheLaterClock(unittest.TestCase):
    def test_a_child_process_runs_later(self):
        got = _probe(_golden._env(DAYS))
        self.assertIn(got[0], _later())
        self.assertEqual(got[0], got[1])
        self.assertEqual(got[0], got[2])
        self.assertGreaterEqual(got[3], int(datetime.datetime.now().timestamp()) + DAYS * 86400 - 5)

    def test_a_date_is_still_a_date(self):
        self.assertEqual(_probe(_golden._env(DAYS))[4:8], [True, True, True, "datetime"])

    def test_a_subclass_keeps_its_own_checks_and_a_real_date_pickles(self):
        """TJ-2771: only the two replaced classes accept the real ones."""
        self.assertEqual(_probe(_golden._env(DAYS))[8:12],
                         [False, True, "2024-01-02", "2024-01-02 00:00:00"])

    def test_a_real_datetime_keeps_its_fold_and_a_copy_its_class(self):
        """TJ-2783: pickle, copy and deepcopy keep a real datetime's fold; a
        copy of a real date or datetime is of its own class, as without the
        shim."""
        code = ("import copy, datetime, json, pickle\n"
                "R = [c for c in type(datetime.datetime.now()).__mro__ if c.__name__ == 'datetime'][-1]\n"
                "real = R(2024, 11, 3, 1, 30, fold=1)\n"
                "print(json.dumps([pickle.loads(pickle.dumps(real)).fold, copy.copy(real).fold, "
                "copy.deepcopy(real).fold, type(copy.copy(real)) is type(real), "
                "type(copy.deepcopy(real.date())) is type(real.date()), "
                "str(pickle.loads(pickle.dumps(real.date())))]))")
        self.assertEqual(_probe(_golden._env(DAYS), code), [1, 1, 1, True, True, "2024-11-03"])

    def test_pandas_loads_only_when_imported_and_works(self):
        """TJ-2779: not imported up front; imported, under the real classes."""
        env = _golden._env(DAYS)
        self.assertFalse(_probe(env)[12])
        if importlib.util.find_spec("pandas") is None:
            self.skipTest("no pandas")
        code = ("import datetime, importlib.resources, json, pandas\nt = pandas.Timestamp('2024-03-01')\n"
                "print(json.dumps([str(t.date()), isinstance(t, datetime.datetime), "
                "datetime.date.today().isoformat(), "
                "len(list(importlib.resources.files('pandas').iterdir())), "
                "hasattr(pandas.__loader__, 'get_data')]))")
        got, idle = _probe(env, code), _probe(_golden._env(0), code)
        self.assertEqual(got[:2], ["2024-03-01", True])
        self.assertIn(got[2], _later())
        # TJ-2785: pandas's own loader, its package data readable.
        self.assertEqual(got[3:], idle[3:])
        self.assertGreater(got[3], 0)

    def test_it_chains_with_the_open_tracer_either_way(self):
        """TJ-2772: two shims on the path load each other once, no recursion;
        both act, whichever comes first (TJ-2787: the tracer writes its start)."""
        for first, second in (("_clockshift", "_opentrace"), ("_opentrace", "_clockshift")):
            with self.subTest(first=first), tempfile.TemporaryDirectory() as trace:
                env = _golden._env(DAYS)
                env["PYTHONPATH"] = os.pathsep.join((str(_SHIMS / first), str(_SHIMS / second),
                                                     env["PYTHONPATH"]))
                env["TAXJSON_TRACE_OPENS"] = trace
                self.assertIn(_probe(env)[0], _later())
                logs = list(Path(trace).glob("*.jsonl"))
                self.assertEqual(len(logs), 1)
                self.assertEqual(json.loads(logs[0].read_text(encoding="utf-8").splitlines()[0])["ev"],
                                 "start")

    def test_idle_with_no_days_or_zero(self):
        """TJ-2780: the shim loaded, its variable unset or 0: today's clock;
        TJ-2786: and nothing replaced (the real classes and clock, no copyreg
        hook, no pandas finder)."""
        code = ("import copyreg, datetime, json, sys, time\n"
                "print(json.dumps([datetime.date.today().isoformat(), type(datetime.date) is type, "
                "type(datetime.datetime) is type, type(time.time).__name__, "
                "[c.__name__ for c in copyreg.dispatch_table if c.__module__ == 'datetime'], "
                "[type(f).__name__ for f in sys.meta_path if 'Real' in type(f).__name__]]))")
        for days in (None, "0"):
            with self.subTest(days=days):
                env = _golden._env(DAYS)
                if days is None:
                    env.pop("TAXJSON_TEST_CLOCK_DAYS")
                else:
                    env["TAXJSON_TEST_CLOCK_DAYS"] = days
                self.assertIn(str(_golden._CLOCKSHIFT), env["PYTHONPATH"])
                got = _probe(env, code)
                self.assertIn(got[0], _later(0))
                self.assertEqual(got[1:], [True, True, "builtin_function_or_method", [], []])
        # The same probe sees the shim act when the variable is set.
        self.assertNotEqual(_probe(_golden._env(DAYS), code)[1:], [True, True, "builtin_function_or_method", [], []])

    def test_the_old_sum_adds_one_float_at_a_time(self):
        """TJ-2791: on Python 3.12 or later, tests/_oldsum gives sum() the
        3.11 float addition; off, or with the variable 0, the builtin."""
        code = "import json\nprint(json.dumps([sum([0.1] * 10), sum([1, 2]), sum([[1], [2]], [])]))"
        old = _golden._env(0, old_sum=True)
        self.assertIn(str(_golden._OLDSUM), old["PYTHONPATH"])
        self.assertEqual(_probe(old, code), [0.9999999999999999, 3, [1, 2]])
        idle = dict(old, TAXJSON_TEST_OLD_SUM="0")
        builtin = [sum([0.1] * 10), 3, [1, 2]]
        self.assertEqual(_probe(idle, code), builtin)
        if sys.version_info >= (3, 12):
            self.assertEqual(builtin[0], 1.0)

    def test_a_runs_own_stages_run_later(self):
        """TJ-2780, TJ-2782: the wash radar is a stage `tjs run` starts
        through its dispatch, here as its own process (TAXJSON_DISPATCH:
        in-process is the default); the open tracer shows that process wrote
        the radar's JSON, and its date (masked in the books) is the later one."""
        with tempfile.TemporaryDirectory() as tmp:
            root, trace = Path(tmp) / "p", Path(tmp) / "trace"
            trace.mkdir()
            shutil.copytree(_golden.GOLDEN / "options" / "project", root,
                            ignore=shutil.ignore_patterns(*_golden._GENERATED))
            _golden._make_private(root)
            env = _golden._env(DAYS)
            env["PYTHONPATH"] = os.pathsep.join((str(_SHIMS / "_opentrace"), env["PYTHONPATH"]))
            env.update(TAXJSON_TRACE_OPENS=str(trace), TAXJSON_DISPATCH="subprocess")
            r = _golden._tjs(["run", "--no-input"], root, env)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
            writers = {}
            for log in trace.glob("*.jsonl"):
                recs = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
                for rec in recs:
                    if rec["ev"] == "rename" and (rec["dst"] or "").endswith("wash_radar_margin.json"):
                        writers[log.stem] = recs[0]["argv"]
            radar = json.loads((root / "reports" / "wash_radar_margin.json").read_text(encoding="utf-8"))
        self.assertEqual(len(writers), 1, writers)
        self.assertTrue(any("wash_radar" in a for a in next(iter(writers.values()))), writers)
        self.assertIn(radar["as_of_date"][:10], _later())


def _case(book):
    def test(self):
        problems = _golden.compare(book, clock_days=DAYS, old_sum=True)
        self.assertFalse(problems, _golden.report(book, problems) + f"\n(the clock {DAYS} days later: "
                         "mask what reads it in tests/_golden.py _STAMPS; and Python 3.11's float "
                         "sum(): a total added with sum() must not move a cent, TJ-2791)")
    return test


for _book in _golden.books():
    if _golden.meta(_book)["kind"] == "project":
        _name = "TestLater_" + _book.replace("-", "_")
        globals()[_name] = type(_name, (unittest.TestCase,), {"test_same_answer": _case(_book)})


if __name__ == "__main__":
    unittest.main()
