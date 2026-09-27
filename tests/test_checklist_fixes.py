"""`taxjson checklist` fixes from the 2026-09 filing audit: detectors that
said "done" on nothing, a form-export step that compared nothing, a DONE
mark that hid a finding, US and sheltered-only projects, and the CLI's
mark/undo/reset/--json/--only/--walk edges."""
import argparse
import builtins
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

from taxjson.lib import checklist as cl

REPO_ROOT = Path(__file__).resolve().parent.parent


class FakeSub:
    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, argv, timeout=900):
        self.calls.append(argv)
        return self.table.get(argv[0], (0, "", ""))


def _ctx(root, table, cfg=None):
    cfg = cfg or {"settings": {"year": 2025, "country": "canada"},
                  "accounts": {"margin": {"type": "taxable"},
                               "rrsp": {"type": "sheltered"}}}
    return cl.Ctx(root=root, cfg=cfg, year=2025, today=date(2026, 9, 23),
                  run_sub=FakeSub(table))


class TestDetectors(unittest.TestCase):
    def test_missing_history_without_books_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            r = cl.d_missing_history(_ctx(Path(td), {"find-missing-history": (
                1, "", "taxjson find-missing-history: no base files in work (run `taxjson run` first).")}))
        self.assertEqual(r.status, "blocked")
        self.assertIn("no base files", r.detail)

    def test_option_boundary_detector(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            r = cl.d_option_boundary(_ctx(root, {"option-boundary": (
                1, "", "taxjson option-boundary: NOT CHECKED — no taxable base files")}))
            self.assertEqual(r.status, "blocked")
            self.assertIn("NOT CHECKED", r.detail)
            doc = {"rows": [{"action": "ATTENTION: 2025 is locked ...", "attention": True}],
                   "timing": "grant", "since_explicit": True}
            r = cl.d_option_boundary(_ctx(root, {"option-boundary": (0, json.dumps(doc), "")}))
            self.assertEqual(r.status, "attention")
            self.assertIn("1 need attention", r.detail)
            doc = {"rows": [], "timing": "grant", "since_explicit": False}
            r = cl.d_option_boundary(_ctx(root, {"option-boundary": (0, json.dumps(doc), "")}))
            self.assertEqual(r.status, "attention")
            self.assertIn("option_grant_timing_since", r.detail)
            doc["since_explicit"] = True
            self.assertEqual(cl.d_option_boundary(
                _ctx(root, {"option-boundary": (0, json.dumps(doc), "")})).status, "done")

    def test_form_export_compares_totals(self):
        fe = {"form": "schedule3", "rows": [{}, {}],
              "totals": {"proceeds_all": 1000.0, "gain_all": 250.0}}
        good = {"accounts": [{"account": "margin", "realized": 250.0},
                             {"account": "rrsp", "realized": 999.0}],
                "filing": {"totals": {"proceeds": 1000.0, "gain": 250.0}}}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            r = cl.d_form_export(_ctx(root, {"form-export": (0, json.dumps(fe), ""),
                                             "sum": (0, json.dumps(good), "")}))
            self.assertEqual(r.status, "done", r.detail)
            self.assertIn("250.00 equals FOR THE RETURN", r.detail)
            bad = json.loads(json.dumps(good))
            bad["filing"]["totals"]["gain"] = 200.0
            bad["accounts"][0]["realized"] = 300.0
            r = cl.d_form_export(_ctx(root, {"form-export": (0, json.dumps(fe), ""),
                                             "sum": (0, json.dumps(bad), "")}))
            self.assertEqual(r.status, "attention")
            self.assertIn("Schedule 3 gain 250.00 vs FOR THE RETURN 200.00", r.detail)
            self.assertIn("vs realized 300.00", r.detail)
            us = {"form": "8949", "part_I": [{}], "part_II": [],
                  "part_I_totals": {"proceeds": 1000.0, "gain": 250.0},
                  "part_II_totals": {"proceeds": 0.0, "gain": 0.0}}
            r = cl.d_form_export(_ctx(root, {"form-export": (0, json.dumps(us), ""),
                                             "sum": (0, json.dumps(good), "")}))
            self.assertEqual(r.status, "done", r.detail)

    def test_t5008_attention_names_the_mismatch(self):
        rep = {"clean": False, "counts": {"ok": 3, "mismatch": 2, "missing_from_computed": 1,
                                          "missing_from_slip": 0},
               "rows": [{"symbol": "SHOP.TO", "status": "MISMATCH"},
                        {"symbol": "RY.TO", "status": "OK"},
                        {"symbol": "BNS.TO", "status": "MISSING_FROM_COMPUTED"}]}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "inputs" / "slips").mkdir(parents=True)
            (root / "inputs" / "slips" / "t5008.csv").write_text("x\n")
            sub = FakeSub({"reconcile-slips": (1, json.dumps(rep), "")})
            ctx = cl.Ctx(root=root, cfg={"settings": {"country": "canada"}, "accounts": {}},
                         year=2025, today=date(2026, 9, 23), run_sub=sub)
            r = cl.d_t5008(ctx)
        self.assertEqual(r.status, "attention")
        self.assertIn("2 mismatch", r.detail)
        self.assertIn("SHOP.TO MISMATCH", r.detail)
        self.assertNotIn("Amounts are compared", r.detail)
        self.assertIn("--json", sub.calls[0])


class TestCountryAndScope(unittest.TestCase):
    def test_us_project_names_and_na(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = {"settings": {"year": 2025, "country": "usa"},
                   "accounts": {"margin": {"type": "taxable"}}}
            res = {r.id: r for r in cl.evaluate(_ctx(Path(td), {}, cfg), quick=True)}
            for sid in ("option-boundary", "t1135", "noa"):
                self.assertEqual(res[sid].status, "n/a", sid)
            text = cl.render(list(res.values()), 2025, "usa", quick=True)
            self.assertIn("1099-B slips reconcile", text)
            self.assertIn("Form 8949 rows exported", text)
            self.assertNotIn("T5008", text)
            self.assertNotIn("line 40500", text)
            self.assertNotIn("Schedule 3", text)
            doc = cl.to_json(list(res.values()), 2025, "usa")
            self.assertEqual({s["id"]: s for s in doc["steps"]}["t5008"]["title"],
                             "1099-B slips reconcile to the computed dispositions")

    def test_sheltered_only_project_is_na_not_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = {"settings": {"year": 2025, "country": "canada"},
                   "accounts": {"rrsp": {"type": "sheltered"}}}
            res = {r.id: r for r in cl.evaluate(_ctx(Path(td), {}, cfg), quick=True)}
        for sid in cl.TAXABLE_ONLY:
            self.assertEqual(res[sid].status, "n/a", sid)
        self.assertNotEqual(res["run-clean"].status, "n/a")


def _project(root):
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\nbase_currency = "CAD"\n'
        '[accounts.margin]\ntype = "taxable"\n')
    (root / "work").mkdir()


def _cli(root, *a):
    return subprocess.run([sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
                           "checklist", *a], cwd=REPO_ROOT, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


class TestCommandEdges(unittest.TestCase):
    def test_mark_edges(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            r = _cli(root, "--note", "x")
            self.assertNotEqual(r.returncode, 0); self.assertIn("--note goes with --done or --skip", r.stderr)
            r = _cli(root, "--undo", "fees")
            self.assertEqual(r.returncode, 0); self.assertIn("had no mark", r.stdout)
            r = _cli(root, "--reset")
            self.assertEqual(r.returncode, 0); self.assertIn("nothing to reset", r.stdout)
            r = _cli(root, "--done", "fees", "--note", "n", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual(doc["recorded"], [{"step": "fees", "mark": "done", "changed": True, "note": "n"}])
            r = _cli(root, "--undo", "fees", "--json")
            self.assertTrue(json.loads(r.stdout)["recorded"][0]["changed"])
            r = _cli(root, "--reset", "--json")
            self.assertEqual(json.loads(r.stdout)["recorded"][0]["mark"], "reset")
            r = _cli(root, "--only", "bogus")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("ids: inputs-frozen", r.stderr)

    def test_walk_reprompts_and_quit_exits_1_with_summary(self):
        from taxjson.bin.taxjson_run import _checklist_walk
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            ctx = _ctx(root, {}, {"settings": {"year": 2025, "country": "canada"},
                                  "accounts": {"margin": {"type": "taxable"}}})
            answers = iter(["x", "q"])
            out = io.StringIO()
            with mock.patch.object(builtins, "input", lambda *_: next(answers)), \
                    redirect_stdout(out), mock.patch.object(cl, "stderr_progress", lambda *a: None):
                with self.assertRaises(SystemExit) as cm:
                    _checklist_walk(ctx, cl, ["fees"])
            self.assertEqual(cm.exception.code, 1)
            text = out.getvalue()
            self.assertIn("unknown key 'x' — [d]one  [s]kip", text)
            self.assertIn("FILING CHECKLIST — tax year 2025", text)
            # [d]one closes the only open step: exit normally.
            answers = iter(["d", ""])
            with mock.patch.object(builtins, "input", lambda *_: next(answers)), \
                    redirect_stdout(io.StringIO()):
                _checklist_walk(ctx, cl, ["fees"])


if __name__ == "__main__":
    unittest.main()
