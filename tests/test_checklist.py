"""`taxjson checklist` — the filing checklist with auto-detected steps and
manual marks (lib/checklist.py + the CLI wrapper)."""
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from taxjson.lib import checklist as cl

REPO_ROOT = Path(__file__).resolve().parent.parent

CFG = {"settings": {"year": 2025, "country": "canada", "base_currency": "CAD"},
       "accounts": {"margin": {"type": "taxable", "holdings": ["h.toml"]},
                    "rrsp": {"type": "sheltered"},
                    "crypto": {"type": "taxable", "crypto": True}}}


def _project(root: Path, *, activity_to="2026-02-02", with_reports=True):
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
        '[accounts.margin]\ntype = "taxable"\nholdings = ["h.toml"]\n'
        '[accounts.rrsp]\ntype = "sheltered"\n'
        '[accounts.crypto]\ntype = "taxable"\ncrypto = true\n')
    for a in ("margin", "rrsp", "crypto"):
        (root / "inputs" / a).mkdir(parents=True)
    (root / "inputs" / "margin" / "m.csv").write_text("x\n")
    (root / "inputs" / "crypto" / "cb_x.csv").write_text("x\n")
    (root / "work").mkdir()
    rows = [{"action": "BUYSELL", "date": "2025-03-03", "date_settle": "2025-03-04",
             "symbol": "X.TO", "quantity": 10, "account": "margin"},
            {"action": "ADJUST", "date": "2025-06-01", "date_settle": "2025-06-01",
             "symbol": "X.TO", "quantity": 0, "account": "margin"},
            {"action": "BUYSELL", "date": activity_to, "date_settle": activity_to,
             "symbol": "X.TO", "quantity": -10, "account": "margin"}]
    (root / "work" / "margin_base.json").write_text(json.dumps({"transactions": rows}))
    (root / "work" / "margin_gains_wash.json").write_text(json.dumps({"transactions": [
        {"date": "2025-05-05", "disallowed_amount": 100.0, "permanently_disallowed": 0.0},
        {"date": "2024-05-05", "disallowed_amount": 0.0, "permanently_disallowed": 999.0}]}))
    if with_reports:
        (root / "reports").mkdir()
        (root / "reports" / "margin.sum").write_text("DIAGNOSTICS\nvalidation: 0 error(s)\n")


class FakeSub:
    """Scripted `taxjson <sub>` results: {first argv token: (code, out, err)}."""
    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, argv, timeout=900):
        self.calls.append(argv)
        return self.table.get(argv[0], (0, "", ""))


def _ctx(root, table, today=date(2026, 9, 23)):
    return cl.Ctx(root=root, cfg=CFG, year=2025, today=today, run_sub=FakeSub(table))


class TestDetectors(unittest.TestCase):
    def test_inputs_frozen_and_stage_one(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            ctx = _ctx(root, {})
            self.assertEqual(cl.d_inputs_frozen(ctx).status, "done")
            self.assertEqual(cl.d_sheltered_inputs(ctx).status, "attention")     # rrsp folder empty
            (root / "inputs" / "rrsp" / "q.csv").write_text("x\n")
            self.assertEqual(cl.d_sheltered_inputs(ctx).status, "done")
            self.assertEqual(cl.d_crypto_inputs(ctx).status, "done")
            r = cl.d_roc_entered(ctx)
            self.assertEqual(r.status, "manual"); self.assertIn("1 ADJUST", r.detail)
            self.assertEqual(cl.d_inputs_committed(ctx).status, "attention")    # not a git repo

    def test_inputs_frozen_year_still_open_vs_january_missing(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root, activity_to="2025-12-20")
            self.assertEqual(cl.d_inputs_frozen(_ctx(root, {}, today=date(2026, 1, 10))).status, "todo")
            self.assertEqual(cl.d_inputs_frozen(_ctx(root, {}, today=date(2026, 3, 1))).status, "attention")

    def test_run_clean_reads_validation_and_freshness(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            ctx = _ctx(root, {})
            self.assertEqual(cl.d_run_clean(ctx).status, "done")
            (root / "reports" / "margin.sum").write_text("validation: 2 error(s)\n")
            self.assertIn("2 validation error", cl.d_run_clean(ctx).detail)
            (root / "reports" / "margin.sum").write_text("ok\n")
            (root / "work" / "pending_elections.json").write_text('{"x": 1}')
            self.assertIn("pending elections", cl.d_run_clean(ctx).detail)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root, with_reports=False)
            self.assertEqual(cl.d_run_clean(_ctx(root, {})).status, "blocked")

    def test_subcommand_backed_detectors(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            fmh = ("AFFECTS 2025 - missing basis distorts this year's gain\n"
                   "Symbol  Account Cur PeakShort\n"
                   "AMZN.US                  margin     CAD      -40.0000 2025-11-03\n"
                   "NOT relevant to 2025\nZZZ.US   margin  CAD  -1 2024-01-01\n")
            audit_bad = "  pipeline tie-out       10 tied, 1 MISMATCHED, 0 not found  ✗\n"
            audit_ok = "  pipeline tie-out       10 tied, 0 MISMATCHED, 0 not found  ✓\n"
            ctx = _ctx(root, {"sanity": (1, "7 discrepancy(ies).", ""),
                              "find-missing-history": (0, fmh, ""),
                              "elect": (0, "No pending elections (...)\n", ""),
                              "audit": (0, audit_bad, ""),
                              "option-boundary": (0, "2 item(s) require an amended return (T1-ADJ)\n", ""),
                              "t1135": (0, json.dumps({"filing_required": True}), ""),
                              "form-export": (0, "a\nb\n", "")})
            self.assertEqual((cl.d_sanity(ctx).status, cl.d_sanity(ctx).detail), ("attention", "7 discrepancy(ies)."))
            r = cl.d_missing_history(ctx)
            self.assertEqual(r.status, "attention"); self.assertIn("AMZN.US (margin)", r.detail); self.assertNotIn("ZZZ", r.detail)
            self.assertEqual(cl.d_elections(ctx).status, "done")
            self.assertEqual(cl.d_audit(ctx).status, "attention")
            self.assertEqual(cl.d_option_boundary(ctx).status, "attention")
            self.assertEqual(cl.d_t1135(ctx).status, "manual")
            self.assertEqual(cl.d_form_export(ctx).status, "done")
            ctx2 = _ctx(root, {"audit": (0, audit_ok, ""),
                               "option-boundary": (0, "No amended return is required by these contracts\n", "")})
            self.assertEqual(cl.d_audit(ctx2).detail, "10 disposition(s) tied")
            self.assertEqual(cl.d_option_boundary(ctx2).status, "done")
            self.assertEqual(cl.d_t5008(ctx2).status, "todo")                     # no slip file
            (root / "inputs" / "slips").mkdir(); (root / "inputs" / "slips" / "t5008.csv").write_text("x\n")
            ctx3 = _ctx(root, {"reconcile-slips": (1, "3 mismatch(es)", "")})
            r = cl.d_t5008(ctx3); self.assertEqual(r.status, "attention"); self.assertIn("t5008.csv", r.detail)
            self.assertEqual(cl.d_t5008(_ctx(root, {})).status, "done")

    def test_wash_reviewed_counts_only_the_tax_year(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            r = cl.d_wash_reviewed(_ctx(root, {}))
            self.assertEqual(r.status, "done"); self.assertIn("100.00 denied", r.detail)   # 2024's permanent denial ignored
            (root / "work" / "margin_gains_wash.json").write_text(json.dumps({"transactions": [
                {"date": "2025-05-05", "disallowed_amount": 50.0, "permanently_disallowed": 50.0}]}))
            self.assertEqual(cl.d_wash_reviewed(_ctx(root, {})).status, "manual")

    def test_filed_lock_paths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            self.assertEqual(cl.d_filed_lock(_ctx(root, {})).status, "todo")
            (root / "filed").mkdir(); (root / "filed" / "2025.json").write_text("{}")
            self.assertEqual(cl.d_filed_lock(_ctx(root, {"check-filed": (1, "DRIFT", "")})).status, "attention")
            self.assertEqual(cl.d_filed_lock(_ctx(root, {})).status, "done")
            self.assertEqual(cl.d_lock_committed(_ctx(root, {})).status, "attention")  # not a repo


class TestOverridesAndRender(unittest.TestCase):
    def test_evaluate_applies_marks_and_keeps_findings_visible(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            ctx = _ctx(root, {"sanity": (1, "7 discrepancy(ies).", "")})
            cl.set_override(root, 2025, "sanity", "done", note="trades after the export")
            cl.set_override(root, 2025, "fees", "skipped")
            with self.assertRaises(KeyError):
                cl.set_override(root, 2025, "nope", "done")
            res = {r.id: r for r in cl.evaluate(ctx, quick=True, only=None)}
            self.assertEqual(res["sanity"].effective, "done")
            self.assertEqual(res["sanity"].status, "todo")                # --quick skipped the detector
            res = {r.id: r for r in cl.evaluate(ctx, only=["sanity", "fees"])}
            self.assertEqual((res["sanity"].effective, res["sanity"].finding), ("done", "7 discrepancy(ies)."))
            self.assertTrue(res["fees"].passed)
            text = cl.render(list(res.values()), 2025, "canada")
            self.assertIn("marked done: trades after the export  !! detector: 7 discrepancy(ies).", text)
            self.assertIn("[~] fees", text)
            cl.set_override(root, 2025, "sanity", None)
            self.assertIsNone(cl.evaluate(ctx, only=["sanity"])[0].override)
            # Marks recorded for another year are ignored, not applied.
            state = cl.load_state(root); state["year"] = 2024; cl.save_state(root, state, 2024)
            self.assertIsNone({r.id: r for r in cl.evaluate(ctx, only=["fees"])}["fees"].override)

    def test_json_and_summary_counts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            res = cl.evaluate(_ctx(root, {}), quick=True)
            self.assertEqual(len(res), len(cl.STEPS))
            doc = cl.to_json(res, 2025, "canada")
            self.assertFalse(doc["all_passed"])
            self.assertEqual({s["id"] for s in doc["steps"]}, {s[0] for s in cl.STEPS})
            text = cl.render(res, 2025, "canada", quick=True)
            self.assertTrue(text.startswith("FILING CHECKLIST — tax year 2025 (canada) — quick:"))
            for num, name in cl.STAGES:
                self.assertIn(f"{num}. {name}", text)


class TestCommand(unittest.TestCase):
    def test_cli_marks_and_report(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            def cli(*a):
                return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root), "checklist", *a],
                                      cwd=REPO_ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL)
            r = cli("--done", "fees", "--note", "12,303.57 interest")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("fees marked done", r.stdout)
            self.assertEqual(json.loads((root / "checklist.json").read_text())["overrides"]["fees"]["note"], "12,303.57 interest")
            r = cli("--quick", "--json")
            self.assertEqual(r.returncode, 1, r.stderr)            # open steps remain
            doc = json.loads(r.stdout)
            self.assertEqual({s["id"]: s for s in doc["steps"]}["fees"]["override"], "done")
            r = cli("--done", "bogus")
            self.assertNotEqual(r.returncode, 0); self.assertIn("unknown step", r.stderr)
            r = cli("--walk")
            self.assertNotEqual(r.returncode, 0); self.assertIn("needs a terminal", r.stderr)
            r = cli("--reset"); self.assertEqual(r.returncode, 0)
            self.assertFalse((root / "checklist.json").exists())


if __name__ == "__main__":
    unittest.main()
