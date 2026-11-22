"""The run's readers the checklist's steps ask come in on the Ctx
(lib/checklist.RunHelpers, built by cli/checklist_run): lib imports nothing
from bin, the commands or the pipeline (step 8 of the architecture plan).
A Ctx or a question_answers call without them is refused, never judged
without the run's own check, and the refusal stops the command: as a
"blocked" item close-year would drop it and lock without the check
(TJ-3532)."""
import ast
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from _run_patch import patch_run
from taxjson.cli import file as cli_file
from taxjson.cli.checklist_run import checklist_run_helpers
from taxjson.lib import checklist as cl
import _paths

SRC = _paths.REPO / "src" / "taxjson"

_CFG = {"settings": {"year": 2025, "country": "canada"},
        "accounts": {"coins": {"type": "taxable", "crypto": True}},
        "distributions": [{"symbol": "ZZFND"}]}


def _ctx(root, run=None):
    return cl.Ctx(root=root, cfg=_CFG, year=2025, today=date(2026, 3, 1),
                  run_sub=lambda *a, **k: (0, "", ""), run=run)


class TestRunHelpers(unittest.TestCase):
    def test_a_ctx_without_them_is_refused_where_a_step_needs_one(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(cl.RunHelpersMissing, "checklist_run_helpers"):
                cl.d_roc_entered(_ctx(Path(td)))
            # crypto-sends, once there is transfer evidence to judge (TJ-3534)
            (Path(td) / "work").mkdir()
            (Path(td) / "work" / "coins_kraken_transfers.json").write_text("{}")
            with self.assertRaisesRegex(cl.RunHelpersMissing, "checklist_run_helpers"):
                cl.d_crypto_sends(_ctx(Path(td)))

    def test_fx_cash_answers_without_them_are_refused(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(cl.RunHelpersMissing, "checklist_run_helpers"):
                cl.question_answers(Path(td), _CFG, "fx-cash")

    def test_the_refusal_stops_the_list_not_blocks_the_item(self):
        def needs_them(ctx):
            return cl._run_helpers(ctx)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(cl.RunHelpersMissing):
                cl._run_detectors(_ctx(Path(td)), ["roc-entered", "audit"], "canada", None, 1)
            # The slow checks' threads too (two of them, two at a time).
            with mock.patch.dict(cl.DETECTORS, {"audit": needs_them,
                                                "sanity": lambda ctx: cl.Result("sanity", "done", "")}), \
                    self.assertRaises(cl.RunHelpersMissing):
                cl._run_detectors(_ctx(Path(td)), ["sanity", "audit"], "canada", None, 2)
            # Any other detector error is still the item's (blocked).
            with mock.patch.dict(cl.DETECTORS, {"audit": lambda ctx: 1 / 0}):
                got = cl._run_detectors(_ctx(Path(td)), ["audit"], "canada", None, 1)
            self.assertEqual(got["audit"].status, "blocked")

    def test_close_years_check_passes_them(self):
        seen = []
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(cl, "evaluate",
                                  side_effect=lambda ctx, **kw: seen.append(ctx) or []):
            cli_file._close_year_attention(Path(td), _CFG, 2025)
        self.assertEqual(len(seen), 1)
        self.assertIsInstance(seen[0].run, cl.RunHelpers)

    def test_every_ctx_the_commands_build_passes_them(self):
        # Each Ctx and question_answers call, with a value (not None: TJ-3537).
        # The one exception judges the run's state only (d_run_clean).
        missing = []
        for p in sorted((SRC / "cli").glob("*.py")):
            tree = ast.parse(p.read_text("utf-8"))
            for fn in ast.walk(tree):
                if not isinstance(fn, ast.FunctionDef):
                    continue
                for n in ast.walk(fn):
                    if not (isinstance(n, ast.Call) and getattr(
                            n.func, "attr", getattr(n.func, "id", "")) in ("Ctx", "question_answers")):
                        continue
                    run = [k.value for k in n.keywords if k.arg == "run"]
                    if ((not run or (isinstance(run[0], ast.Constant) and run[0].value is None))
                            and (p.name, fn.name) != ("common.py", "_run_state_problems")):
                        missing.append(f"cli/{p.name}:{n.lineno} ({fn.name})")
        self.assertEqual(sorted(set(missing)), [])

    def test_a_step_rule_does_not_swallow_the_refusal(self):
        def rule(ctx, facts):
            return cl._run_helpers(ctx)
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(cl.STEP_RULES, {"install": rule}), \
                self.assertRaises(cl.RunHelpersMissing):
            cl.evaluate(_ctx(Path(td)), only=["install"])

    def test_a_step_reaches_the_patched_reader(self):
        # patch_run patches every binding of the run's code: the helper reads
        # cli/checklist_run's when the step runs, not one taken at build time.
        run = checklist_run_helpers()
        with tempfile.TemporaryDirectory() as td, \
                patch_run("_double_roc_warnings",
                          return_value=["ZZFND 2025-06-30: entered twice"]) as fake:
            r = cl.d_roc_entered(_ctx(Path(td), run))
        self.assertEqual(r.status, "attention")
        self.assertIn("ZZFND 2025-06-30: entered twice", r.detail)
        self.assertEqual(fake.call_args.args[0], Path(td))

    def test_the_readers_are_the_run_codes(self):
        run = checklist_run_helpers()
        with patch_run("_crypto_broker_files", return_value={"coins": []}) as f, \
                patch_run("_fx_cash_status", return_value={"status": "ok"}) as g:
            self.assertEqual(run.crypto_broker_files(Path("/synthetic"), _CFG),
                             {"coins": []})
            self.assertEqual(run.fx_cash_status(Path("/synthetic"), Path("/w")),
                             {"status": "ok"})
        f.assert_called_once_with(Path("/synthetic"), _CFG)
        g.assert_called_once_with(Path("/synthetic"), Path("/w"))


if __name__ == "__main__":
    unittest.main()
