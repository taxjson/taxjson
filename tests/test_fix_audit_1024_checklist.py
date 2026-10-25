"""The 2026-10-24 audit's checklist findings (A01-A04, A14-A19): the
kept verdicts' provenance and staleness (lib/checklist.save_results,
load_results, checklist_fingerprint), the stored question (A03), the
marks written before the command line was checked (A15), the lenient
config loader (A16, A17), the walk's exit (A18) and the store's lock
(A19). Synthetic projects only (tests/test_checklist._project)."""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from pathlib import Path
from unittest.mock import patch

from taxjson.bin import taxjson_run as run
from taxjson.lib import checklist as cl
from taxjson.lib.dispatch import python_module_argv

from test_checklist import _ctx, _project

REPO_ROOT = Path(__file__).resolve().parent.parent


def _cli(root, *a):
    """`taxjson -C ROOT checklist ...` as a child of THIS tree's code
    (PYTHONPATH names src/, as tests/test_fix_a2_checklist does)."""
    cmd = python_module_argv("taxjson.bin.taxjson_run",
                             ["-C", str(root), "checklist", *a])
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          env={"TAXJSON_WIDTH": "0", "NO_COLOR": "1",
                               "PATH": "/usr/bin:/bin", "HOME": str(root),
                               "PYTHONPATH": str(REPO_ROOT / "src")})


def _store(ctx):
    return json.loads((ctx.cache / cl.RESULTS_FILE).read_text())


def _ctx_like(ctx, cfg, year):
    return cl.Ctx(root=ctx.root, cfg=cfg, year=year, today=ctx.today,
                  run_sub=ctx.run_sub)


class TestKeptVerdicts(unittest.TestCase):
    def test_a01_partial_refresh_keeps_the_other_verdicts_stale(self):
        """A01 Partial checklist refresh clears staleness of unrefreshed
        checks: a `--run --only ID` stamped today's fingerprint on every
        kept verdict; each keeps its own basis now."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root); ctx = _ctx(root, {})
            cl.save_results(ctx, [cl.Result("audit", "done", "old audit")])
            (root / "ticker.map").write_text("GLOBAL ZZQ.US ZZR.US\n")
            self.assertTrue(cl.load_results(ctx)[1].stale)
            cl.save_results(ctx, [cl.Result("install", "done", "installed")])
            rows, info = cl.load_results(ctx)
            self.assertEqual(len(info.stale), 1, info.stale)
            self.assertIn("the inputs changed (ticker.map)", info.stale[0])
            self.assertEqual(info.stale_steps, ["audit"])
            self.assertEqual(info.stored, 2)
            text = cl.render_summary(rows, 2025, "canada", info=info)
            self.assertIn("Since then the inputs changed (ticker.map) (1 of "
                          "the 2 verdicts shown are from before): `tjs "
                          "checklist --run` re-checks.", text)
            # The store: two bases, each step naming its own.
            doc = _store(ctx)
            self.assertEqual(len(doc["bases"]), 2)
            self.assertNotEqual(doc["steps"]["audit"]["basis"],
                                doc["steps"]["install"]["basis"])
            self.assertEqual(set(doc["bases"]),
                             {e["basis"] for e in doc["steps"].values()})
            # Refreshing the stale step clears it; one basis is left.
            cl.save_results(ctx, [cl.Result("audit", "done", "new audit")])
            rows, info = cl.load_results(ctx)
            self.assertEqual(info.stale, [])
            self.assertEqual(len(_store(ctx)["bases"]), 1)

    def test_a01_a_store_an_older_release_wrote_is_read(self):
        """A01: a store without `bases` — every entry has the document's
        provenance; a partial refresh keeps it for the kept entries."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root); ctx = _ctx(root, {})
            cl.save_results(ctx, [cl.Result("audit", "done", "old audit")])
            path = ctx.cache / cl.RESULTS_FILE
            doc = json.loads(path.read_text())
            doc.pop("bases")
            doc["steps"]["audit"].pop("basis")
            path.write_text(json.dumps(doc))
            self.assertEqual(cl.load_results(ctx)[1].stale, [])
            (root / "ticker.map").write_text("GLOBAL ZZQ.US ZZR.US\n")
            self.assertTrue(cl.load_results(ctx)[1].stale)
            cl.save_results(ctx, [cl.Result("install", "done", "installed")])
            _rows, info = cl.load_results(ctx)
            self.assertEqual(info.stale_steps, ["audit"])

    def test_a02_another_years_verdicts_are_not_carried_into_this_one(self):
        """A02 Partial checklist refresh carries last-year verdicts into
        new year: the kept verdicts of another year are dropped when
        this year's are stored."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root); ctx = _ctx(root, {})
            cl.save_results(ctx, [cl.Result("audit", "done", "old audit")])
            cfg = deepcopy(ctx.cfg)
            cfg["settings"]["year"] = 2026
            later = _ctx_like(ctx, cfg, 2026)
            cl.save_results(later, [cl.Result("install", "done", "installed")])
            rows, _info = cl.load_results(later, only=["audit"])
            self.assertEqual((rows[0].status, rows[0].detail),
                             ("todo", cl.NOT_CHECKED))
            doc = _store(later)
            self.assertEqual(doc["year"], 2026)
            self.assertEqual(sorted(doc["steps"]), ["install"])

    def test_a03_a_later_mark_answers_the_stored_question(self):
        """A03 Cached unanswered question remains unanswerable after
        marking current answers done: the store keeps the detector's own
        detail and question flag, not the marked view."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root); ctx = _ctx(root, {})
            cl.set_override(root, 2025, "export-coverage", "done",
                            answers=["old"])
            r = cl.Result("export-coverage", "attention", "gap",
                          question=True, answers=["new"])
            cl.apply_override(
                r, cl.load_state(root)["overrides"]["export-coverage"])
            self.assertEqual(r.effective, "attention")   # `new` not answered
            self.assertIn("not answered by the done mark", r.detail)
            cl.save_results(ctx, [r])
            e = _store(ctx)["steps"]["export-coverage"]
            self.assertEqual((e["detail"], e["question"]), ("gap", True))
            cl.set_override(root, 2025, "export-coverage", "done",
                            answers=["new"])
            loaded, _ = cl.load_results(ctx, only=["export-coverage"])
            self.assertEqual(loaded[0].effective, "done")
            self.assertEqual(loaded[0].detail, "gap")

    def test_a04_a_kept_n_a_gives_way_when_the_step_applies(self):
        """A04 Stored not-applicable verdict survives adding taxable
        account: the shape's n/a is decided on load and never stored."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root); ctx = _ctx(root, {})
            cfg = deepcopy(ctx.cfg)
            for acfg in cfg["accounts"].values():
                acfg["type"] = "sheltered"
            prior = _ctx_like(ctx, cfg, 2025)
            found = cl.evaluate(prior, only=["t1135"])
            self.assertEqual(found[0].status, "n/a")
            cl.save_results(prior, found)
            self.assertNotIn("t1135", _store(ctx)["steps"])
            rows, _ = cl.load_results(ctx, only=["t1135"])
            self.assertEqual((rows[0].status, rows[0].detail),
                             ("todo", cl.NOT_CHECKED))
            # Still n/a while the shape says so (live).
            rows, _ = cl.load_results(prior, only=["t1135"])
            self.assertEqual(rows[0].status, "n/a")
            # A store an older release wrote the shape's n/a into.
            path = ctx.cache / cl.RESULTS_FILE
            doc = json.loads(path.read_text())
            doc["steps"]["t1135"] = {
                "status": "n/a", "checked": "2026-01-01T00:00:00",
                "detail": "no taxable account — nothing to report"}
            path.write_text(json.dumps(doc))
            rows, _ = cl.load_results(ctx, only=["t1135"])
            self.assertEqual(rows[0].status, "todo")
            # A detector's own n/a is a verdict and is kept.
            cl.save_results(ctx, [cl.Result("crypto-sends", "n/a",
                                            "no crypto account configured")])
            rows, _ = cl.load_results(ctx, only=["crypto-sends"])
            self.assertEqual(rows[0].status, "n/a")

    def test_a14_a_changed_holdings_snapshot_or_slip_is_stale(self):
        """A14 Checklist freshness ignores holdings and other detector-
        only inputs: the fingerprint of the kept verdicts covers the
        files only the checks read."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root); ctx = _ctx(root, {})
            cl.save_results(ctx, [cl.Result("sanity", "done", "matches")])
            self.assertEqual(cl.load_results(ctx)[1].stale, [])
            (root / "h.toml").write_text(           # [accounts.margin] holdings
                '[[holding]]\nsymbol = "ZZQ.US"\nquantity = 999\n')
            info = cl.load_results(ctx)[1]
            self.assertEqual(len(info.stale), 1, info.stale)
            self.assertIn("the inputs changed (h.toml)", info.stale[0])
            cl.save_results(ctx, [cl.Result("sanity", "done", "matches")])
            self.assertEqual(cl.load_results(ctx)[1].stale, [])
            (root / "holdings").mkdir()
            (root / "holdings" / "rrsp.toml").write_text(
                '[[holding]]\nsymbol = "ZZQ.US"\nquantity = 5\n')
            (root / "inputs" / "slips").mkdir()
            (root / "inputs" / "slips" / "slips.toml").write_text("")
            info = cl.load_results(ctx)[1]
            self.assertIn("the inputs changed (rrsp.toml, slips.toml)",
                          info.stale[0])
            fp = cl.checklist_fingerprint(ctx)
            self.assertIn("holdings/rrsp.toml", fp)
            self.assertIn("inputs/slips/slips.toml", fp)
            self.assertIn("h.toml", fp)

    def test_a19_two_partial_saves_at_once_keep_every_verdict(self):
        """A19 Concurrent partial checklist runs lose each other's
        verdicts: the read, merge and write hold the project lock."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root); ctx = _ctx(root, {})
            cl.save_results(ctx, [cl.Result("install", "done")])
            original = cl._load_results_doc
            first = threading.Lock()
            first.acquire()

            def slow_read(c):
                doc = original(c)
                if first.locked():
                    # The first reader dawdles: unlocked, the second
                    # read the same document meanwhile and one of the
                    # two writes lost the other's step.
                    first.release()
                    time.sleep(0.3)
                return doc
            with patch.object(cl, "_load_results_doc", side_effect=slow_read):
                with ThreadPoolExecutor(2) as pool:
                    list(pool.map(
                        lambda sid: cl.save_results(ctx, [cl.Result(sid, "done")]),
                        ["audit", "sanity"]))
            self.assertEqual(sorted(_store(ctx)["steps"]),
                             ["audit", "install", "sanity"])


class TestCommand(unittest.TestCase):
    def test_a15_a_bad_command_line_writes_no_mark(self):
        """A15 Checklist commits marks before validating --only: an
        unknown --only step or a bad -j stops before any mark is
        written."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            p = _cli(root, "--done", "sanity", "--run", "--only", "not-a-step")
            self.assertEqual(p.returncode, 1, p.stderr)
            self.assertIn("unknown step 'not-a-step'", p.stderr)
            self.assertFalse((root / cl.STATE_FILE).exists())
            p = _cli(root, "--done", "sanity", "--run", "-j", "0")
            self.assertEqual(p.returncode, 1, p.stderr)
            self.assertIn("-j takes a number of 1 or more", p.stderr)
            self.assertFalse((root / cl.STATE_FILE).exists())
            p = _cli(root, "--done", "sanity", "--walk")   # no terminal
            self.assertEqual(p.returncode, 1, p.stderr)
            self.assertIn("--walk needs a terminal", p.stderr)
            self.assertFalse((root / cl.STATE_FILE).exists())
            p = _cli(root, "--done", "sanity")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertTrue((root / cl.STATE_FILE).exists())

    def test_a16_an_account_that_is_no_table_is_the_loaders_error(self):
        """A16 Lenient checklist loader accepts non-table accounts then
        crashes: `margin = 42` is the strict loader's own message, not an
        AttributeError."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                '[accounts]\nmargin = 42\n')
            p = _cli(root, "--run", "--only", "configure")
            self.assertNotEqual(p.returncode, 0)
            self.assertNotIn("Traceback", p.stderr)
            self.assertNotIn("AttributeError", p.stderr)
            self.assertIn("[accounts.margin] must be a table", p.stderr)
            # The library itself reads such an entry as no account.
            ctx = cl.Ctx(root=root, cfg={"settings": {"year": 2025,
                                                      "country": "canada"},
                                         "accounts": {"margin": 42}},
                         year=2025, today=date(2026, 9, 23),
                         run_sub=lambda argv, timeout=900: (0, "", ""))
            self.assertEqual(ctx.accounts, {})
            self.assertEqual(cl._project_shape(ctx)[2], False)

    def test_a17_a_work_link_leaving_the_project_is_refused(self):
        """A17 Checklist bypasses output-directory symlink refusal and
        writes outside project: the checklist refuses the link as every
        command does, and the store never writes through one."""
        with tempfile.TemporaryDirectory() as td:
            parent = Path(td)
            root = parent / "project"; root.mkdir()
            outside = parent / "outside"; outside.mkdir()
            (root / "taxjson.toml").write_text(
                '[settings]\nyear = 2025\ncountry = "canada"\n'
                '[accounts.margin]\ntype = "taxable"\n')
            (root / "work").symlink_to(outside, target_is_directory=True)
            p = _cli(root, "--run", "--only", "configure")
            self.assertEqual(p.returncode, 2, p.stderr)
            self.assertIn("symlinks to outside the project", p.stderr)
            self.assertEqual(list(outside.iterdir()), [])
            ctx = cl.Ctx(root=root, cfg={"settings": {"year": 2025,
                                                      "country": "canada"},
                                         "accounts": {"margin": {
                                             "type": "taxable"}}},
                         year=2025, today=date(2026, 9, 23),
                         run_sub=lambda argv, timeout=900: (0, "", ""))
            with self.assertRaises(OSError) as cm:
                cl.save_results(ctx, [cl.Result("install", "done")])
            self.assertIn("outside the project", str(cm.exception))
            self.assertEqual(list(outside.iterdir()), [])

    def test_a18_the_walk_exits_1_while_a_step_is_open(self):
        """A18 Checklist walk exits zero after DONE leaves a factual
        finding unresolved: the walk's exit follows the verdicts as
        kept, with the marks applied."""
        def walk(ctx, only, answers, **patches):
            out = io.StringIO()
            code = 0
            with contextlib.ExitStack() as st:
                for name, value in patches.items():
                    st.enter_context(patch.object(cl, name, **value))
                st.enter_context(patch("builtins.input", side_effect=answers))
                st.enter_context(contextlib.redirect_stdout(out))
                try:
                    run._checklist_walk(ctx, cl, only)
                except SystemExit as e:
                    code = e.code
            return code, out.getvalue()

        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root); ctx = _ctx(root, {})
            # A question step: DONE recorded for other keys than the
            # ones asked — the step stays open, so does the exit.
            asked = [cl.Result("export-coverage", "attention", "gap",
                               question=True, answers=["99900001|zz|2025-12-31"])]
            code, _text = walk(ctx, ["export-coverage"], ["d", ""],
                               evaluate={"side_effect": lambda *a, **k: asked},
                               question_answers={"return_value": ["other"]})
            self.assertEqual(code, 1)
            res = cl.load_results(ctx, only=["export-coverage"])[0][0]
            self.assertEqual(res.effective, "attention")
            self.assertEqual(res.override, "done")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root); ctx = _ctx(root, {})
            # A factual finding: [d] is refused, Enter passes it over.
            found = [cl.Result("sanity", "attention", "mismatch")]
            code, text = walk(ctx, ["sanity"], ["d", "", ""],
                              evaluate={"side_effect": lambda *a, **k: found})
            self.assertEqual(code, 1)
            self.assertIn("a done mark would not show over it", text)
            self.assertEqual(
                cl.load_results(ctx, only=["sanity"])[0][0].effective,
                "attention")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root); ctx = _ctx(root, {})
            # Everything settled: no exit.
            fine = [cl.Result("sanity", "done", "positions tie")]
            code, _text = walk(ctx, ["sanity"], [],
                               evaluate={"side_effect": lambda *a, **k: fine})
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
