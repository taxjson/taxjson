"""`taxjson checklist`: the default view is the list then the summary;
a step is named by its id or its number (`--done 18`); `--section N`
shows (or checks, or walks) one section (lib/checklist.resolve_id,
resolve_section, section_ids, render)."""
import json
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import checklist as cl

from test_checklist import _ctx, _project
from test_checklist_run import OK, _cli


class TestNames(unittest.TestCase):
    def test_a_step_by_its_id_or_its_number(self):
        ids = cl.item_ids()
        self.assertEqual(cl.resolve_id("roc-entered"), "roc-entered")
        self.assertEqual(cl.resolve_id("1"), ids[0])
        self.assertEqual(cl.resolve_id(str(len(ids))), ids[-1])
        self.assertEqual(cl.resolve_id(" 5 "), ids[4])
        for bad in ("0", str(len(ids) + 1), "bogus", "-1", ""):
            with self.assertRaises(KeyError, msg=bad):
                cl.resolve_id(bad)

    def test_a_section_by_its_number_or_name(self):
        self.assertEqual(cl.resolve_section("4"), "Fill the gaps")
        self.assertEqual(cl.resolve_section("Fill the gaps"), "Fill the gaps")
        self.assertEqual(cl.resolve_section("fill the  GAPS"), "Fill the gaps")
        self.assertEqual(cl.resolve_section("fill"), "Fill the gaps")
        self.assertEqual(cl.resolve_section("10"), "Year end")
        self.assertEqual(cl.resolve_section("Check"), "Check")
        # "s" starts two sections (Set up, Share safely): no guess.
        for bad in ("0", "11", "bogus", "s", ""):
            with self.assertRaises(KeyError, msg=bad):
                cl.resolve_section(bad)

    def test_section_ids_are_the_sections_steps_in_order(self):
        ids = cl.item_ids()
        seen = []
        for sec in cl.SECTIONS:
            sids = cl.section_ids(sec)
            self.assertTrue(sids, sec)
            self.assertEqual(sids, [i for i in ids if i in sids])
            seen += sids
        self.assertEqual(seen, ids)
        self.assertEqual(cl.section_label("Fill the gaps"), "4. Fill the gaps")


class TestView(unittest.TestCase):
    def test_default_is_the_list_then_the_summary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            results = cl.evaluate(_ctx(root, OK))
            text = cl.render(results, 2025, "canada", width_=100)
            lines = text.splitlines()
            heads = [i for i, ln in enumerate(lines)
                     if ln in {f"{k}. {s.upper()}"
                               for k, s in enumerate(cl.SECTIONS, 1)}]
            self.assertEqual(len(heads), len(cl.SECTIONS))
            table = lines.index("SECTION              DONE  ATTENTION  "
                                "BLOCKED  TO DO  CONFIRM  READ")
            self.assertGreater(table, max(heads))
            rows = [ln for ln in lines[table:]
                    if any(ln.startswith(f"{k}. {s} ")
                           for k, s in enumerate(cl.SECTIONS, 1))]
            self.assertEqual(len(rows), len(cl.SECTIONS))
            self.assertIn("one section: `--section N`", text)
            self.assertIn("(the step's id or number)", text)
            # The summary alone (after a --walk) is the same block.
            summary = cl.render_summary(results, 2025, "canada", width_=100)
            self.assertIn(lines[table], summary)
            self.assertNotIn("1. SET UP", summary)
            self.assertIn("More: tjs checklist (every step, then this "
                          "summary)", summary)

    def test_a_section_scope_is_in_the_title(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            results = cl.evaluate(_ctx(root, OK),
                                  only=cl.section_ids("Fill the gaps"))
            text = cl.render(results, 2025, "canada", width_=100,
                             scope="4. Fill the gaps")
            self.assertTrue(text.startswith(
                "CHECKLIST — tax year 2025 (canada) — 4. Fill the gaps: "),
                text.splitlines()[0])
            self.assertIn("4. FILL THE GAPS", text)
            self.assertNotIn("1. SET UP", text)
            self.assertNotIn("5. TIDY THE CONFIG", text)


class TestCommand(unittest.TestCase):
    def test_marks_by_number(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            ids = cl.item_ids()
            n = ids.index("roc-entered") + 1
            r = _cli(root, "--done", str(n), "--note", "checked the T3s")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("roc-entered marked done", r.stdout)
            marks = cl.load_state(root)["overrides"]
            self.assertEqual(marks["roc-entered"]["status"], "done")
            self.assertEqual(marks["roc-entered"]["note"], "checked the T3s")
            r = _cli(root, "--undo", str(n))
            self.assertIn("roc-entered mark removed", r.stdout)
            self.assertNotIn("roc-entered",
                             cl.load_state(root)["overrides"])
            r = _cli(root, "--skip", "tt-lines", "--done",
                     str(ids.index("noa") + 1), "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual([(m["step"], m["mark"]) for m in
                              json.loads(r.stdout)["recorded"]],
                             [("noa", "done"), ("tt-lines", "skipped")])
            # A number out of range, like an unknown id, writes nothing.
            r = _cli(root, "--done", str(len(ids) + 1), "--done", "install")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn(f"unknown step '{len(ids) + 1}'", r.stderr)
            self.assertIn(f"or the step's number, 1-{len(ids)}", r.stderr)
            self.assertNotIn("install",
                             cl.load_state(root)["overrides"])
            # --only by number: that step alone.
            doc = json.loads(_cli(root, "--only", "2", "--json").stdout)
            self.assertEqual([s["id"] for s in doc["steps"]], [ids[1]])

    def test_section_shows_checks_and_refuses(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); _project(root)
            r = _cli(root, "--section", "4")
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertTrue(r.stdout.startswith(
                "CHECKLIST — tax year 2025 (canada) — 4. Fill the gaps: "),
                r.stdout.splitlines()[0])
            self.assertIn("4. FILL THE GAPS", r.stdout)
            self.assertNotIn("1. SET UP", r.stdout)
            self.assertNotIn("3. Build the books", r.stdout)
            self.assertIn("4. Fill the gaps ", r.stdout)       # the table
            for name in ("fill", "Fill the gaps", "FILL THE GAPS"):
                doc = json.loads(_cli(root, "--section", name,
                                      "--json").stdout)
                self.assertEqual([s["id"] for s in doc["steps"]],
                                 cl.section_ids("Fill the gaps"), name)
            # --run --section checks that section alone and keeps it.
            r = _cli(root, "--run", "--section", "1", "--json")
            self.assertEqual(r.returncode, 0, r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual({s["section"] for s in doc["steps"]}, {"Set up"})
            self.assertTrue(doc["all_passed"])
            kept = json.loads((root / "work" / cl.RESULTS_FILE).read_text())
            self.assertEqual(set(kept["steps"]), set(cl.section_ids("Set up")))
            whole = json.loads(_cli(root, "--json").stdout)
            self.assertEqual(len(whole["steps"]), len(cl.item_ids()))
            self.assertEqual({s["status"] for s in whole["steps"]
                              if s["section"] == "Set up"}, {"done"})
            self.assertIn(cl.NOT_CHECKED,
                          {s["detail"] for s in whole["steps"]
                           if s["section"] == "Check"})
            r = _cli(root, "--section", "bogus")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("unknown section 'bogus'", r.stderr)
            self.assertIn("4. Fill the gaps", r.stderr)
            r = _cli(root, "--section", "4", "--only", "10")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("one or the other", r.stderr)

    def test_outside_a_project_one_section_of_the_guide(self):
        with tempfile.TemporaryDirectory() as td:
            r = _cli(Path(td), "--section", "2")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("— 2. Get your files", r.stdout.splitlines()[0])
            self.assertIn("2. GET YOUR FILES", r.stdout)
            self.assertNotIn("1. SET UP", r.stdout)
            doc = json.loads(_cli(Path(td), "--section", "year",
                                  "--json").stdout)
            self.assertEqual({s["section"] for s in doc["steps"]},
                             {"Year end"})
            self.assertFalse(doc["in_project"])
            self.assertEqual(list(Path(td).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
