"""The 2026-10-25 audit's checklist findings: a `--done` over a question
step's finding that asks nothing (fx-cash's NOT RELIABLE default ledger,
option-boundary's amendment, a blocked check) is refused, and `--force`
records the forced mark, as for every other step (first-project#1,
diff-tooling#1); a question awaiting its answer is still answered by a
plain `--done`; another tax year's kept verdict refuses no mark
(diff-tooling#2). lib/checklist.kept_verdict, finding_over_done;
taxjson_run.cmd_checklist, _checklist_walk. Synthetic projects only
(tests/test_checklist._project)."""
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from taxjson.bin import taxjson_run as run
from taxjson.lib import checklist as cl
from taxjson.lib.dispatch import python_module_argv

from test_checklist import FakeSub, _ctx, _project

REPO_ROOT = Path(__file__).resolve().parent.parent

# `tjs fx-cash --json` of the default ledger (v1) that saw foreign
# cash move: its NOT RELIABLE headline (made-up figures).
FX_V1 = json.dumps({
    "ledger": "v1", "active": True, "overdrafts_in_year": 3,
    "reliable": False,
    "headline": "FX on foreign cash: NOT RELIABLE for 2025 — 3 in-year "
                "overdrafts (412.50 USD); do not file this figure"})
# `tjs option-boundary --json`: a contract whose premium needs a
# T1-ADJ (an amendment, no question).
OB_AMEND = json.dumps({"rows": [{
    "account": "margin", "symbol": "ZZQ.US 2025-01-17 C 10",
    "written": "2024-11-04", "action": "T1-ADJ 2024"}]})


def _cli(root, *a):
    """`tjs -C ROOT checklist ...` as a child of THIS tree's code."""
    cmd = python_module_argv("taxjson.bin.taxjson_run",
                             ["-C", str(root), "checklist", *a])
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          env={"TAXJSON_WIDTH": "0", "NO_COLOR": "1",
                               "PATH": "/usr/bin:/bin", "HOME": str(root),
                               "PYTHONPATH": str(REPO_ROOT / "src")})


def _marks(root):
    p = root / cl.STATE_FILE
    return json.loads(p.read_text())["overrides"] if p.exists() else {}


def _steps_json(root):
    p = _cli(root, "--json")
    return {s["id"]: s for s in json.loads(p.stdout)["steps"]}, p


class TestQuestionStepFindings(unittest.TestCase):
    def test_first_project_1_fx_cash_and_option_boundary_findings_take_force(self):
        """first-project#1 `checklist --done fx-cash --force` stores a
        plain done mark: the step stays [!] and the default ledger's NOT
        RELIABLE finding can never be accepted from the command line; a
        plain `--done fx-cash` is accepted over attention. Now a plain
        --done is refused over a question step's finding that asks
        nothing (fx-cash v1, option-boundary's T1-ADJ, a blocked check)
        and --force records `"forced": true`, shown [f]."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            ctx = _ctx(root, {"fx-cash": (0, FX_V1, ""),
                              "option-boundary": (0, OB_AMEND, "")})
            fx = cl.d_fx_cash(ctx)
            ob = cl.d_option_boundary(ctx)
            self.assertEqual((fx.status, fx.question), ("attention", False))
            self.assertEqual((ob.status, ob.question), ("attention", False))
            self.assertIn("T1-ADJ", ob.detail)
            for r in (fx, ob):
                self.assertEqual(cl.finding_over_done(r), "attention")
            blocked = cl.Result("t5-t3", "blocked", "no books — run "
                                "`tjs run`")
            self.assertEqual(cl.finding_over_done(blocked), "blocked")
            cl.save_results(ctx, [fx, ob, blocked])

            # A plain --done is refused, naming --force; nothing written.
            for sid, what in (("fx-cash", "attention"),
                              ("option-boundary", "attention"),
                              ("t5-t3", "blocked")):
                p = _cli(root, "--done", sid)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn(f"{sid} is marked {what} by the last check",
                              p.stderr)
                self.assertIn(f"--done {sid} --force", p.stderr)
                self.assertFalse((root / cl.STATE_FILE).exists())

            # --force records the forced mark, said so.
            p = _cli(root, "--done", "fx-cash,option-boundary,t5-t3",
                     "--force", "--note", "worked out by hand")
            self.assertEqual(p.returncode, 0, p.stderr)
            for sid in ("fx-cash", "option-boundary", "t5-t3"):
                self.assertIn(f"{sid} marked done (forced)", p.stdout)
            self.assertIn("The check still says attention: shown [f]",
                          p.stdout)
            self.assertNotIn("nothing to force", p.stdout)
            marks = _marks(root)
            for sid in ("fx-cash", "option-boundary", "t5-t3"):
                self.assertTrue(marks[sid].get("forced"), marks[sid])
                self.assertEqual(marks[sid]["note"], "worked out by hand")
                self.assertNotIn("answers", marks[sid])

            # Shown [f], counted as done, the finding beside it.
            rows = {r.id: r for r in cl.load_results(ctx)[0]}
            self.assertEqual(rows["fx-cash"].effective, "forced")
            self.assertTrue(rows["fx-cash"].passed)
            self.assertIn("NOT RELIABLE", rows["fx-cash"].finding)
            self.assertEqual(rows["option-boundary"].effective, "forced")
            self.assertEqual(rows["t5-t3"].effective, "forced")
            steps, _p = _steps_json(root)
            self.assertEqual(steps["fx-cash"]["effective"], "forced")
            self.assertEqual(steps["fx-cash"]["override"], "forced")
            p = _cli(root, "--only", "fx-cash")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("[f]", p.stdout)
            self.assertIn("marked done (forced): worked out by hand",
                          p.stdout)

    def test_first_project_1_a_question_awaiting_its_answer_is_answered(self):
        """first-project#1 (the answer flow kept): a question step whose
        kept verdict IS a question is answered by a plain --done, as
        before; --force there forces nothing (a forced mark would accept
        every later question too) and says so."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root); ctx = _ctx(root, {})
            asked = cl.Result("option-boundary", "attention",
                              "1 contract written in 2024 and closed in "
                              "2025 — was its premium reported in 2024?",
                              question=True, answers=[])
            self.assertIsNone(cl.finding_over_done(asked))
            # A question a done mark answered for other keys still asks:
            # the detector's own flag decides (own_question).
            asked2 = deepcopy(asked)
            asked2.answers = ["margin|ZZQ.US|2024-11-04"]
            cl.apply_override(asked2, {"status": "done", "answers": []})
            self.assertFalse(asked2.question)
            self.assertIsNone(cl.finding_over_done(asked2))
            cl.save_results(ctx, [asked])
            p = _cli(root, "--done", "option-boundary")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("option-boundary marked done (recorded", p.stdout)
            self.assertNotIn("forced", json.dumps(_marks(root)))
            rows, _ = cl.load_results(ctx, only=["option-boundary"])
            self.assertEqual(rows[0].effective, "done")
            p = _cli(root, "--done", "option-boundary", "--force")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("option-boundary marked done (recorded", p.stdout)
            self.assertIn("(nothing to force: the done mark answers "
                          "option-boundary's question)", p.stdout)
            self.assertNotIn("forced", _marks(root)["option-boundary"])

    def test_first_project_1_the_walk_refuses_done_over_fx_cash_v1(self):
        """first-project#1 (the walk): `[d]` over fx-cash's NOT RELIABLE
        finding is refused as `--done` is, naming [f]; `[f]` records the
        forced mark; `[d]` over a question still answers it."""
        def walk(ctx, only, answers, found, keys=None):
            out = io.StringIO()
            code = 0
            with contextlib.ExitStack() as st:
                st.enter_context(patch.object(
                    cl, "evaluate", side_effect=lambda *a, **k: [found]))
                st.enter_context(patch.object(
                    cl, "question_answers", return_value=keys or []))
                st.enter_context(patch("builtins.input",
                                       side_effect=answers))
                st.enter_context(contextlib.redirect_stdout(out))
                try:
                    run._checklist_walk(ctx, cl, only)
                except SystemExit as e:
                    code = e.code
            return code, out.getvalue()

        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            ctx = _ctx(root, {"fx-cash": (0, FX_V1, "")})
            fx = cl.d_fx_cash(ctx)
            code, text = walk(ctx, ["fx-cash"], ["d", "", "f", "by hand"],
                              fx)
            self.assertIn("fx-cash is attention: a done mark would not "
                          "show over it — [f] accepts it as it is", text)
            self.assertIn("recorded: fx-cash done (forced)", text)
            self.assertEqual(code, 0, text)
            self.assertTrue(_marks(root)["fx-cash"]["forced"])
            self.assertEqual(
                cl.load_results(ctx, only=["fx-cash"])[0][0].effective,
                "forced")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root); ctx = _ctx(root, {})
            key = "v2|2025|12.34"
            asked = cl.Result("fx-cash", "attention", "v2 computed — review "
                              "it", question=True, answers=[key])
            code, text = walk(ctx, ["fx-cash"], ["d", ""], asked,
                              keys=[key])
            self.assertIn("recorded: fx-cash done", text)
            self.assertNotIn("would not show", text)
            self.assertEqual(code, 0, text)
            self.assertEqual(_marks(root)["fx-cash"]["answers"], [key])

    def test_diff_tooling_1_close_year_accepts_the_forced_fx_cash(self):
        """diff-tooling#1 `checklist --done fx-cash --force` writes a plain
        done mark: the step stays [!], the list exits 1 and close-year
        still refuses. Now the forced mark settles the step for
        close-year's check of the steps before the lock."""
        table = {"fx-cash": (0, FX_V1, "")}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root); ctx = _ctx(root, table)
            cl.save_results(ctx, [cl.d_fx_cash(ctx)])
            def attention():
                # close-year's check of the steps before the lock, the
                # sub-commands scripted (fx-cash's NOT RELIABLE).
                with patch.object(cl, "default_run_sub",
                                  side_effect=lambda r: FakeSub(table)), \
                        contextlib.redirect_stderr(io.StringIO()):
                    return dict(run._close_year_attention(
                        root, ctx.cfg, 2025))
            found = attention()
            self.assertIn("fx-cash", found)
            self.assertIn("NOT RELIABLE", found["fx-cash"])
            p = _cli(root, "--done", "fx-cash", "--force")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("fx-cash marked done (forced)", p.stdout)
            self.assertNotIn("fx-cash", attention())


class TestAnotherYear(unittest.TestCase):
    def test_diff_tooling_2_another_years_verdict_refuses_no_mark(self):
        """diff-tooling#2 `checklist --done` is refused over another tax
        year's kept verdict (stored_status ignores the store's year), and
        --force then records a forced mark on a step never checked. Now
        another year's verdicts are no kept verdict: a plain mark is
        recorded, --force forces nothing; this year's still refuse."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root); ctx = _ctx(root, {})
            cfg = deepcopy(ctx.cfg)
            cfg["settings"]["year"] = 2024
            before = cl.Ctx(root=root, cfg=cfg, year=2024, today=ctx.today,
                            run_sub=ctx.run_sub)
            found = cl.Result("missing-history", "attention",
                              "1 position(s) with missing basis affect 2024")
            cl.save_results(before, [found])
            self.assertEqual(cl.stored_status(before, "missing-history"),
                             "attention")
            # taxjson.toml says 2025: the 2024 verdict is none of its.
            self.assertIsNone(cl.kept_verdict(ctx, "missing-history"))
            self.assertIsNone(cl.stored_status(ctx, "missing-history"))
            rows, _info = cl.load_results(ctx, only=["missing-history"])
            self.assertEqual(rows[0].detail, cl.NOT_CHECKED)
            p = _cli(root, "--done", "missing-history")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertNotIn("marked attention", p.stderr)
            self.assertNotIn("forced", _marks(root)["missing-history"])
            p = _cli(root, "--done", "missing-history", "--force")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("(nothing to force: the last check does not flag "
                          "missing-history)", p.stdout)
            self.assertNotIn("forced", _marks(root)["missing-history"])
            # This year's own verdict still refuses the plain mark.
            cl.save_results(ctx, [cl.Result("missing-history", "attention",
                                            "1 position(s) with missing "
                                            "basis affect 2025")])
            self.assertEqual(
                cl.kept_verdict(ctx, "missing-history").status, "attention")
            p = _cli(root, "--done", "missing-history")
            self.assertEqual(p.returncode, 1, p.stdout)
            self.assertIn("missing-history is marked attention by the last "
                          "check", p.stderr)


if __name__ == "__main__":
    unittest.main()
