"""`tjs checklist --run` / `-j`: the checks run only on --run, the
slow ones in parallel, and their verdicts are kept in
work/checklist_results.json; a plain `tjs checklist` shows the kept
verdicts (marks applied as they are now) with when they were made and
what moved since (lib/checklist.save_results, load_results, RunInfo)."""
import json
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from taxjson.lib import checklist as cl
from taxjson.lib.dispatch import python_module_argv

from test_checklist import _ctx, _project

REPO_ROOT = Path(__file__).resolve().parent.parent

# Every slow sub-command answers cleanly, so the verdicts are stable
# between a serial and a parallel run.
OK = {sub: (0, "ok\n", "") for sub in (
    "sanity", "find-missing-history", "elect", "audit", "option-boundary",
    "handoff", "reconcile-slips", "form-export", "t1135", "carryover",
    "fx-cash", "amt", "check-filed", "check-dates", "sum")}


def _cli(root, *a):
    cmd = python_module_argv("taxjson.bin.taxjson_run",
                             ["-C", str(root), "checklist", *a])
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          env={"TAXJSON_WIDTH": "0", "NO_COLOR": "1",
                               "PATH": "/usr/bin:/bin", "HOME": str(root)})


def _verdicts(results):
    return [(r.id, r.status, r.detail) for r in results]


class TestParallel(unittest.TestCase):
    def test_jobs_give_the_serial_verdicts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            serial = _ctx(root, OK)
            parallel = _ctx(root, OK)
            a = cl.evaluate(serial, jobs=1)
            b = cl.evaluate(parallel, jobs=4)
            self.assertEqual(_verdicts(a), _verdicts(b))
            self.assertEqual([r.id for r in b], cl.item_ids())
            # The same sub-commands ran, whatever the order.
            self.assertEqual(sorted(c[0] for c in serial.run_sub.calls),
                             sorted(c[0] for c in parallel.run_sub.calls))
            self.assertTrue(any(c[0] == "audit"
                                for c in parallel.run_sub.calls))

    def test_progress_names_each_slow_check_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            seen = []
            cl.evaluate(_ctx(root, OK), jobs=3,
                        progress=lambda sid, cmd: seen.append(sid))
            self.assertEqual(len(seen), len(set(seen)))
            self.assertTrue(set(seen) <= cl.SLOW, seen)
            self.assertIn("audit", seen)

    def test_default_jobs_is_at_least_one(self):
        self.assertGreaterEqual(cl.default_jobs(), 1)
        self.assertLessEqual(cl.default_jobs(), 4)


class TestStore(unittest.TestCase):
    def test_nothing_kept_lists_the_steps_unchecked(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            results, info = cl.load_results(_ctx(root, OK))
            self.assertTrue(info.never)
            self.assertEqual([r.id for r in results], cl.item_ids())
            checks = [r for r in results if r.id in cl.DETECTORS
                      and r.status != "n/a"]
            self.assertTrue(checks)
            self.assertTrue(all(r.status == "todo" and r.detail == cl.NOT_CHECKED
                                and not r.actionable for r in checks))
            self.assertIsNone(cl.next_result(results))
            text = cl.render_summary(results, 2025, "canada", info=info)
            self.assertIn("Nothing checked yet: `tjs checklist --run`", text)
            self.assertIn("Next: `tjs checklist --run`", text)
            full = cl.render(results, 2025, "canada", info=info)
            # Said once at the top, not under every step.
            self.assertEqual(full.count("Nothing checked yet"), 1)
            self.assertNotIn(cl.NOT_CHECKED, full)
            self.assertFalse((root / "work" / cl.RESULTS_FILE).exists())

    def test_run_keeps_the_verdicts_and_a_load_shows_them(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            ctx = _ctx(root, OK)
            ran = cl.evaluate(ctx)
            doc = cl.save_results(ctx, ran)
            path = root / "work" / cl.RESULTS_FILE
            self.assertTrue(path.is_file())
            self.assertEqual(json.loads(path.read_text())["format"],
                             cl.RESULTS_FORMAT)
            self.assertEqual(doc["year"], 2025)
            kept, info = cl.load_results(_ctx(root, {}))   # no sub-command runs
            self.assertFalse(info.never)
            self.assertEqual(info.stale, [])
            self.assertEqual(_verdicts(kept), _verdicts(ran))
            text = cl.render_summary(kept, 2025, "canada", info=info)
            self.assertIn(f"Checked {info.checked[:16].replace('T', ' ')}", text)
            self.assertIn("`tjs checklist --run` re-checks", text)
            js = cl.to_json(kept, 2025, "canada", info)
            self.assertEqual(js["checked"], info.checked)
            self.assertEqual(js["stale"], [])

    def test_quick_run_keeps_the_slow_verdicts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            ctx = _ctx(root, OK)
            cl.save_results(ctx, cl.evaluate(ctx),
                            now=datetime(2026, 3, 1, 9, 0, 0))
            cl.save_results(ctx, cl.evaluate(ctx, quick=True),
                            now=datetime(2026, 3, 2, 9, 0, 0))
            kept, info = cl.load_results(ctx)
            by = {r.id: r for r in kept}
            self.assertNotEqual(by["audit"].detail, cl.QUICK_SKIPPED)
            self.assertEqual(info.checked, "2026-03-02T09:00:00")
            self.assertEqual(info.oldest, "2026-03-01T09:00:00")
            text = cl.render_summary(kept, 2025, "canada", info=info)
            self.assertIn("Checked 2026-03-02 09:00 (the oldest verdict "
                          "2026-03-01 09:00)", text)

    def test_only_refreshes_one_step_and_keeps_the_rest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            ctx = _ctx(root, OK)
            cl.save_results(ctx, cl.evaluate(ctx))
            later = _ctx(root, {**OK, "sanity": (1, "", "MISMATCH x\n")})
            cl.save_results(later, cl.evaluate(later, only=["sanity"]))
            self.assertEqual([c[0] for c in later.run_sub.calls], ["sanity"])
            kept, _info = cl.load_results(ctx)
            by = {r.id: r for r in kept}
            self.assertEqual(by["sanity"].status, "attention")
            self.assertEqual(by["audit"].status, "done")

    def test_a_changed_input_or_a_rebuilt_book_is_stale(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            ctx = _ctx(root, OK)
            cl.save_results(ctx, cl.evaluate(ctx))
            _kept, info = cl.load_results(ctx)
            self.assertEqual(info.stale, [])
            (root / "inputs" / "margin" / "m.csv").write_text("x\ny\n")
            _kept, info = cl.load_results(ctx)
            self.assertEqual(len(info.stale), 1, info.stale)
            self.assertIn("the inputs changed (m.csv)", info.stale[0])
            cl.record_input_fingerprint(root, ctx.cfg)        # a `tjs run`
            _kept, info = cl.load_results(ctx)
            self.assertTrue(any("the books were rebuilt" in s
                                for s in info.stale), info.stale)
            text = cl.render_summary(_kept, 2025, "canada", info=info)
            self.assertIn("Since then the inputs changed (m.csv); the books "
                          "were rebuilt (`tjs run`): `tjs checklist "
                          "--run` re-checks.", text)

    def test_marks_apply_to_the_kept_verdicts_as_they_are_now(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            ctx = _ctx(root, OK)
            cl.save_results(ctx, cl.evaluate(ctx))
            cl.set_override(root, 2025, "fees", "done", note="statement")
            kept, info = cl.load_results(ctx)
            by = {r.id: r for r in kept}
            self.assertEqual(by["fees"].override, "done")
            self.assertEqual(by["fees"].note, "statement")
            self.assertEqual(info.stale, [])            # a mark is not staleness
            # The store itself never carries a mark.
            doc = json.loads((root / "work" / cl.RESULTS_FILE).read_text())
            self.assertNotIn("override", doc["steps"]["fees"])

    def test_another_years_store_is_not_this_years(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            ctx = _ctx(root, OK)
            cl.save_results(ctx, cl.evaluate(ctx))
            path = root / "work" / cl.RESULTS_FILE
            doc = json.loads(path.read_text())
            doc["year"] = 2024
            path.write_text(json.dumps(doc))
            kept, info = cl.load_results(ctx)
            self.assertTrue(info.never)
            self.assertTrue(all(r.detail == cl.NOT_CHECKED for r in kept
                                if r.id in cl.DETECTORS and r.status != "n/a"))

    def test_a_work_folder_holding_only_the_store_is_no_books(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root, with_reports=False)
            for f in (root / "work").iterdir():
                f.unlink()
            ctx = _ctx(root, OK)
            cl.save_results(ctx, [cl.Result("tips", "review", "")])
            self.assertTrue((root / "work" / cl.RESULTS_FILE).is_file())
            self.assertFalse(cl.has_books(ctx))
            r = cl.DETECTORS["renames"](ctx)
            self.assertEqual((r.status, r.detail),
                             ("blocked", "no work/ — run `tjs run`"))


class TestCommand(unittest.TestCase):
    def test_default_shows_kept_verdicts_and_run_makes_them(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            r = _cli(root)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("Nothing checked yet: `tjs checklist --run`", r.stdout)
            self.assertNotIn("==> ", r.stderr)              # no check ran
            doc = json.loads(_cli(root, "--json").stdout)
            self.assertIsNone(doc["checked"])
            self.assertEqual(doc["stale"], [])
            self.assertFalse((root / "work" / cl.RESULTS_FILE).exists())
            r = _cli(root, "--run", "--quick", "--json")
            self.assertEqual(r.returncode, 1, r.stderr)
            ran = json.loads(r.stdout)
            self.assertIsNotNone(ran["checked"])
            self.assertTrue((root / "work" / cl.RESULTS_FILE).is_file())
            again = json.loads(_cli(root, "--json").stdout)
            self.assertEqual(again["checked"], ran["checked"])
            self.assertEqual([(s["id"], s["status"], s["detail"])
                              for s in again["steps"]],
                             [(s["id"], s["status"], s["detail"])
                              for s in ran["steps"]])
            self.assertIn(f"Checked {ran['checked'][:16].replace('T', ' ')}",
                          _cli(root).stdout)

    def test_quick_and_jobs_without_run_are_a_note(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            r = _cli(root, "--quick")
            self.assertIn("--quick and -j go with --run", r.stderr)
            self.assertIn("Nothing checked yet", r.stdout)

    def test_jobs_spellings_and_floor(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            for flag in ("-j", "--jobs", "--j"):
                r = _cli(root, "--run", "--quick", flag, "2")
                self.assertEqual(r.returncode, 1, (flag, r.stderr))
                self.assertNotIn("error", r.stderr, flag)
            r = _cli(root, "--run", "-j", "0")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("1 or more", r.stderr)

    def test_outside_a_project_run_prints_the_guide(self):
        with tempfile.TemporaryDirectory() as td:
            for args in (("--run",), ("--run", "-j", "2")):
                r = _cli(Path(td), *args)
                self.assertEqual(r.returncode, 0, (args, r.stderr))
                self.assertIn("1. SET UP", r.stdout)
                self.assertEqual(list(Path(td).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
