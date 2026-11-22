"""The golden books' views (tests/_golden.py): the `tjs` commands a project
book runs after its run (book.json `views`), against what they printed and
wrote when recorded. The commands the run never calls hold most of the code's
rounding; a refactor of it leaves these alone too. One class per book, so the
suite can run them side by side and a failure names the book."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _golden

# The books with views. A book's views cannot quietly go missing.
VIEW_BOOKS = {"crypto", "demo", "style-canada", "style-usa", "evidence"}


def _case(book):
    def test(self):
        problems = _golden.compare(book, only="views")
        self.assertFalse(problems, _golden.report(book, problems))
    return test


for _book in sorted(VIEW_BOOKS):
    _name = "TestViews_" + _book.replace("-", "_")
    globals()[_name] = type(_name, (unittest.TestCase,), {"test_views": _case(_book)})


class TestWhichBooksHaveViews(unittest.TestCase):
    def test_the_books_with_views(self):
        self.assertEqual({b for b in _golden.books() if _golden.meta(b).get("views")}, VIEW_BOOKS)

    def test_each_view_is_recorded_in_order(self):
        for book in sorted(VIEW_BOOKS):
            views = _golden.meta(book)["views"]
            expected = _golden.GOLDEN / book / "expected" / "_views"
            index = json.loads((expected / "index.json").read_text(encoding="utf-8"))
            self.assertEqual([(i["view"], i["cwd"], i.get("width", 0), i.get("tool")) for i in index],
                             [(" ".join(_golden.view_args(v)), v.get("cwd", ".") if isinstance(v, dict) else ".",
                               _golden.view_width(v), _golden.view_tool(v)) for v in views], book)
            for v in views:
                name = _golden.view_name(v)
                self.assertTrue(list(expected.glob(f"{name}.stdout.*")), (book, name))

    def test_a_json_view_is_compared_by_value(self):
        """A --json view that printed JSON is recorded as .json: its numbers
        compare to float noise, as the run's JSON files do."""
        for book in sorted(VIEW_BOOKS):
            expected = _golden.GOLDEN / book / "expected" / "_views"
            index = json.loads((expected / "index.json").read_text(encoding="utf-8"))
            for i in index:
                if "--json" in i["view"].split() and i["exit"] == 0:
                    name = _golden.view_name({"argv": i["view"], "cwd": i["cwd"], "width": i.get("width", 0),
                                              "tool": i.get("tool")})
                    self.assertTrue((expected / f"{name}.stdout.json").is_file(), (book, name))


def _done(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class TestHowAViewIsRecorded(unittest.TestCase):
    """_run_views on a made-up project, `tjs` replaced by what each view does."""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.root = Path(d.name) / "p"
        (self.root / "inputs" / "a").mkdir(parents=True)
        (self.root / "inputs" / "a" / "x.csv").write_text("row\n")
        (self.root / "reports").mkdir()
        (self.root / "reports" / "a.sum").write_text("sum\n")
        (self.root / "old.txt").write_text("old\n")

    def run_views(self, views, actions, m=None):
        """actions: {view's first word: f(cwd) -> CompletedProcess}; each
        call's environment in self.envs."""
        calls, self.envs = [], []

        def tjs(argv, cwd, env):
            calls.append((argv, cwd))
            self.envs.append(env)
            return actions[argv[0]](cwd)
        with mock.patch.object(_golden, "_tjs", tjs):
            out = _golden._run_views("b", {**(m or {}), "views": views}, self.root, {}, {str(self.root)})
        return out, calls

    def test_names(self):
        self.assertEqual(_golden.view_name("sum --json"), "sum_json")
        self.assertEqual(_golden.view_name("estimate --province ON"), "estimate_province_ON")
        self.assertEqual(_golden.view_name({"cwd": "../2025", "argv": "handoff --json"}), "2025_handoff_json")
        self.assertEqual(_golden.view_name("form-export --csv s3.csv"), "form-export_csv_s3.csv")
        self.assertEqual(_golden.view_name({"argv": "sum --json", "width": 120}), "sum_json_w120")
        self.assertEqual(_golden.view_name({"argv": "sum", "width": 0}), "sum")

    def test_a_view_at_a_width(self):
        """PLAN step 15: TAXJSON_WIDTH for that view alone, the width in
        index.json and the name (none at 0: the books' views as before)."""
        out, _ = self.run_views(["sum", {"argv": "sum", "width": 120}, "sum --json"],
                                {"sum": lambda cwd: _done("1\n")})
        self.assertEqual([e.get("TAXJSON_WIDTH") for e in self.envs], [None, "120", None])
        self.assertEqual(sorted(n for n in out if ".stdout." in n),
                         ["_views/sum.stdout.txt", "_views/sum_json.stdout.json", "_views/sum_w120.stdout.txt"])
        self.assertEqual(json.loads(out["_views/index.json"]),
                         [{"view": "sum", "cwd": ".", "exit": 0},
                          {"view": "sum", "cwd": ".", "exit": 0, "width": 120},
                          {"view": "sum --json", "cwd": ".", "exit": 0}])

    def test_a_tool_view(self):
        """A tool's document no command prints: the taxjson.bin module run
        with the view's argv instead of `tjs`, named for it, the module in
        index.json; its words (what the byte proof and the views A/B run)
        start with the tool's console script."""
        out, calls = self.run_views([{"tool": "taxjson_sum_gains", "argv": "--json work/x.json"}, "sum"],
                                    {"taxjson-sum-gains": lambda cwd: _done('{"a": 1}\n'),
                                     "sum": lambda cwd: _done()})
        self.assertEqual(calls, [(["taxjson-sum-gains", "--json", "work/x.json"], self.root), (["sum"], self.root)])
        self.assertEqual(out["_views/taxjson_sum_gains_json_work_x.json.stdout.json"], '{"a": 1}\n')
        self.assertEqual(json.loads(out["_views/index.json"]),
                         [{"view": "--json work/x.json", "cwd": ".", "exit": 0, "tool": "taxjson_sum_gains"},
                          {"view": "sum", "cwd": ".", "exit": 0}])
        ran = []
        with mock.patch.object(_golden, "_tool", lambda module, argv, cwd, env: ran.append((module, argv))):
            _golden._tjs(["taxjson-sum-gains", "--json", "x.json"], self.root, {})
            _golden._tjs(["sum", "--json"], self.root, {})
        self.assertEqual(ran, [("taxjson_sum_gains", ["--json", "x.json"]), ("taxjson_run", ["sum", "--json"])])
        for bad in ("taxjson.bin.x", "", "../x", 1, "taxjson_nothing"):
            with self.subTest(tool=bad), self.assertRaisesRegex(AssertionError, "a taxjson.bin module"):
                self.run_views([{"tool": bad, "argv": "--json"}], {})

    def test_a_width_that_is_not_a_whole_number_is_refused(self):
        for width in (-1, "120", 1.5, True):
            with self.subTest(width=width), self.assertRaisesRegex(AssertionError, "a whole number"):
                self.run_views([{"argv": "sum", "width": width}], {"sum": lambda cwd: _done()})

    def test_stdout_stderr_and_the_exit_codes(self):
        out, _ = self.run_views(
            ["sum", "sum --json", "bad --json", "quiet"],
            {"sum": lambda cwd: _done('{"a": 1.5}\n'), "bad": lambda cwd: _done("not json\n", "error: x\n", 2),
             "quiet": lambda cwd: _done()})
        self.assertEqual(out["_views/sum.stdout.txt"], '{"a": 1.5}\n')      # not --json: text
        self.assertEqual(out["_views/sum_json.stdout.json"], '{"a": 1.5}\n')
        self.assertEqual(out["_views/bad_json.stdout.txt"], "not json\n")   # --json, did not parse
        self.assertEqual(out["_views/bad_json.stderr.txt"], "error: x\n")
        self.assertEqual(out["_views/quiet.stdout.txt"], "")
        self.assertNotIn("_views/quiet.stderr.txt", out)                    # only when there is one
        self.assertNotIn("_views/sum.stderr.txt", out)
        self.assertEqual([(i["view"], i["exit"]) for i in json.loads(out["_views/index.json"])],
                         [("sum", 0), ("sum --json", 0), ("bad --json", 2), ("quiet", 0)])

    def test_the_folder_is_masked(self):
        out, _ = self.run_views(["sum"], {"sum": lambda cwd: _done(f"at {self.root}/reports\n")})
        self.assertEqual(out["_views/sum.stdout.txt"], "at <project>/reports\n")

    def test_a_view_runs_in_the_runs_folder_or_its_own(self):
        (self.root / "2024").mkdir()
        _, calls = self.run_views(["sum", {"cwd": "../2025", "argv": "handoff"}],
                                  {"sum": lambda cwd: _done(), "handoff": lambda cwd: _done()},
                                  m={"run_dir": "2024"})
        self.assertEqual([c for _a, c in calls], [self.root / "2024", self.root / "2024" / "../2025"])

    def test_what_the_views_write(self):
        def close(cwd):
            (cwd / "filed").mkdir()
            (cwd / "filed" / "2024.json").write_text('{"closed_at": "2026-11-10T05:26:07"}')
            (cwd / "filed" / "a.sum").write_text("sum\n")              # a copy of reports/a.sum
            (cwd / "work").mkdir()
            (cwd / "work" / "x.state").write_text("stage")             # a stage file: not compared
            (cwd / "old.txt").unlink()
            return _done()
        out, _ = self.run_views(["close-year"], {"close-year": close})
        self.assertEqual(out["_views/files/filed/2024.json"], '{"closed_at": "<stamp>"}')
        self.assertNotIn("_views/files/filed/a.sum", out)
        self.assertEqual(json.loads(out["_views/copies.json"]), {"filed/a.sum": "reports/a.sum"})
        self.assertEqual(out["_views/removed/old.txt"], "")
        self.assertNotIn("_views/files/work/x.state", out)

    def test_an_output_only_view_leaves_its_files_out(self):
        def run(cwd):
            (cwd / "reports" / "b.sum").write_text("b\n")
            (cwd / "reports" / "a.sum").write_text("changed\n")
            return _done("ran\n")

        def later(cwd):
            (cwd / "later.txt").write_text("later\n")
            return _done()
        out, _ = self.run_views([{"argv": "run --no-input", "record": "output"}, "later"],
                                {"run": run, "later": later})
        self.assertEqual(out["_views/run_no-input.stdout.txt"], "ran\n")
        self.assertEqual(sorted(n for n in out if n.startswith("_views/files/")), ["_views/files/later.txt"])

    def test_an_output_only_view_keeps_what_a_recorded_view_wrote_before_and_after_it(self):
        """TJ-2777: the path holds its state from before the output-only view;
        a later recorded view's write to it is recorded."""
        def new(cwd):
            (cwd / "n.toml").write_text("a = 1\n")
            (cwd / "m.toml").write_text("b = 1\n")
            return _done()

        def run(cwd):
            (cwd / "n.toml").write_text("a = 2\n")
            (cwd / "m.toml").write_text("b = 2\n")
            return _done()

        def later(cwd):
            (cwd / "m.toml").write_text("b = 3\n")
            return _done()
        out, _ = self.run_views(["new", {"argv": "run", "record": "output"}, "later"],
                                {"new": new, "run": run, "later": later})
        self.assertEqual(out["_views/files/n.toml"], "a = 1\n")
        self.assertEqual(out["_views/files/m.toml"], "b = 3\n")

    def test_a_rewrite_that_moves_only_a_stamp_is_no_change(self):
        """TJ-2773: decided on the masked text, so it does not depend on the
        clock ticking between the run and the view."""
        (self.root / "reports" / "h.toml").write_text("generated_at = 2026-11-10T05:00:00Z\nx = 1\n")
        (self.root / "reports" / "k.toml").write_text("generated_at = 2026-11-10T05:00:00Z\nx = 1\n")

        def rewrite(cwd):
            (cwd / "reports" / "h.toml").write_text("generated_at = 2026-11-10T05:00:09Z\nx = 1\n")
            (cwd / "reports" / "k.toml").write_text("generated_at = 2026-11-10T05:00:09Z\nx = 2\n")
            return _done()
        out, _ = self.run_views(["rewrite"], {"rewrite": rewrite})
        self.assertNotIn("_views/files/reports/h.toml", out)
        self.assertEqual(out["_views/files/reports/k.toml"], "generated_at = 2000-01-01T00:00:00Z\nx = 2\n")

    def test_a_held_path_rewritten_with_only_a_new_stamp_stays_held(self):
        """TJ-2781: after an output-only view, a recorded view's stamp-only
        rewrite of what that view changed is no change, whether or not the
        clock ticked: the path is not recorded with the output-only content."""
        h = self.root / "reports" / "h.toml"
        h.write_text("generated_at = 2026-11-10T05:00:00Z\nx = 1\n")

        def run(cwd):
            h.write_text("generated_at = 2026-11-10T05:00:00Z\nx = 2\n")
            return _done()
        for stamp in ("05:00:00", "05:00:09"):
            with self.subTest(stamp=stamp):
                def handoff(cwd, stamp=stamp):
                    h.write_text(f"generated_at = 2026-11-10T{stamp}Z\nx = 2\n")
                    return _done()
                h.write_text("generated_at = 2026-11-10T05:00:00Z\nx = 1\n")
                out, _ = self.run_views([{"argv": "run", "record": "output"}, "handoff"],
                                        {"run": run, "handoff": handoff})
                self.assertNotIn("_views/files/reports/h.toml", out)

    def test_views_before_the_first_output_only_one_take_no_snapshot(self):
        """TJ-2789: one snapshot before the views, one after; one more before
        the first output-only view and after each view from it on."""
        real, n = _golden._snapshot, []

        def counted(root):
            n.append(1)
            return real(root)
        views = ["a", "b", "c", {"argv": "run", "record": "output"}, "d"]
        with mock.patch.object(_golden, "_snapshot", counted):
            self.run_views(views, {k: lambda cwd: _done() for k in ("a", "b", "c", "d", "run")})
        self.assertEqual(len(n), 2 + 1 + 2)
        n.clear()
        with mock.patch.object(_golden, "_snapshot", counted):
            self.run_views(["a", "b"], {"a": lambda cwd: _done(), "b": lambda cwd: _done()})
        self.assertEqual(len(n), 2)

    def test_a_removed_stage_file_is_not_recorded(self):
        """TJ-2778: the rest of work/ is left out, removals too."""
        (self.root / "work").mkdir()
        (self.root / "work" / "x.state").write_text("stage")
        (self.root / "work" / "m_gains.json").write_text("{}")

        def clear(cwd):
            (cwd / "work" / "x.state").unlink()
            (cwd / "work" / "m_gains.json").unlink()
            return _done()
        out, _ = self.run_views(["clear"], {"clear": clear})
        self.assertNotIn("_views/removed/work/x.state", out)
        self.assertEqual(out["_views/removed/work/m_gains.json"], "")

    def test_a_digest_is_checked_against_the_file_it_names(self):
        """TJ-2776: a lock's frozen input digests, before the mask."""
        import hashlib
        good = hashlib.sha256(b"row\n").hexdigest()

        def close(cwd):
            (cwd / "filed").mkdir()
            (cwd / "filed" / "2024.json").write_text(json.dumps(
                {"frozen": {"inputs": {"inputs/a/x.csv": good, "inputs/a/y.csv": good,
                                       "inputs/a/gone.csv": "0" * 64}}}, indent=1))
            (cwd / "inputs" / "a" / "y.csv").write_text("other\n")
            return _done()
        out, _ = self.run_views(["close"], {"close": close})
        lock = json.loads(out["_views/files/filed/2024.json"])["frozen"]["inputs"]
        self.assertEqual(lock, {"inputs/a/x.csv": "<sha256 ok>", "inputs/a/y.csv": "<sha256 MISMATCH>",
                                "inputs/a/gone.csv": "<sha256>"})

    def test_two_views_cannot_record_as_one_name(self):
        with self.assertRaisesRegex(AssertionError, "two views record as one name"):
            self.run_views(["sum --json", "sum json"], {"sum": lambda cwd: _done()})

    def test_an_unknown_record_is_refused(self):
        with self.assertRaisesRegex(AssertionError, "all or output"):
            self.run_views([{"argv": "sum", "record": "none"}], {"sum": lambda cwd: _done()})


class TestWhichPartIsCompared(unittest.TestCase):
    """compare(only=...) splits one recording: the run's files, or the views'."""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        golden = Path(d.name)
        patcher = mock.patch.object(_golden, "GOLDEN", golden)
        patcher.start()
        self.addCleanup(patcher.stop)
        (golden / "b" / "expected" / "work").mkdir(parents=True)
        (golden / "b" / "expected" / "_views").mkdir()
        (golden / "b" / "book.json").write_text('{"kind": "project", "views": ["sum"]}')
        self.was = {"work/x_gains.json": "{}", "_views/sum.stdout.txt": "a\n"}
        for n, t in self.was.items():
            (golden / "b" / "expected" / n).write_text(t)

    def compare(self, now, only):
        seen = {}

        def produce(book, views=True, clock_days=0, old_sum=False):
            seen["views"] = views
            return now
        with mock.patch.object(_golden, "produce", produce):
            return _golden.compare("b", only=only), seen["views"]

    def test_the_run_alone_does_not_run_the_views_or_read_them(self):
        problems, views = self.compare({"work/x_gains.json": "{}"}, "run")
        self.assertEqual((problems, views), ([], False))

    def test_the_views_alone_leave_the_run_out(self):
        problems, views = self.compare({"work/x_gains.json": '{"a": 1}', "_views/sum.stdout.txt": "a\n"}, "views")
        self.assertEqual((problems, views), ([], True))

    def test_each_part_sees_its_own_difference(self):
        now = {"work/x_gains.json": '{"a": 1}', "_views/sum.stdout.txt": "b\n"}
        self.assertEqual(self.compare(now, "run")[0], ["work/x_gains.json.a: new: 1"])
        self.assertEqual(self.compare(now, "views")[0], ["_views/sum.stdout.txt[0]: 'a', now 'b'"])
        self.assertEqual(len(self.compare(now, None)[0]), 2)

    def test_an_unknown_part_is_refused(self):
        with self.assertRaises(ValueError):
            _golden.compare("b", only="view")


if __name__ == "__main__":
    unittest.main()
