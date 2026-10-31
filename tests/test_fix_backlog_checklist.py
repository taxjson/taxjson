"""Backlog group `checklist`: the checklist's core, marks, JSON and
wording (lib/checklist.py, taxjson_run.cmd_checklist). Each test cites
its backlog id. Synthetic projects only (tests/test_checklist._project,
`tjs init --demo` / `--single`)."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib import checklist as cl
from taxjson.lib.dispatch import python_module_argv

from test_checklist import _ctx, _project

REPO_ROOT = Path(__file__).resolve().parent.parent


def _tjs(*a, cwd=None, width=0):
    """`tjs ...` as a child of THIS tree's code (`width`: as
    TAXJSON_WIDTH; 120 is a pipe's)."""
    cmd = python_module_argv("taxjson.bin.taxjson_run", list(a))
    return subprocess.run(cmd, cwd=cwd or REPO_ROOT, capture_output=True,
                          text=True, stdin=subprocess.DEVNULL,
                          env={"TAXJSON_WIDTH": str(width), "NO_COLOR": "1",
                               "PATH": "/usr/bin:/bin",
                               "HOME": str(cwd or REPO_ROOT),
                               "TAXJSON_OFFLINE": "1",
                               "PYTHONPATH": str(REPO_ROOT / "src")})


class TestInitHintsRunTheChecks(unittest.TestCase):
    def test_tj0011_init_and_new_year_name_checklist_run(self):
        """TJ-0011: init --demo's Try block, init's NEXT STEPS and
        new-year's said plain `tjs checklist` runs the checks; it
        only shows the kept verdicts (on a first project, every step
        unchecked). Each now names `checklist --run`."""
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            demo = _tjs("init", "--demo", str(top / "demo"), cwd=td)
            self.assertEqual(demo.returncode, 0, demo.stderr)
            self.assertRegex(demo.stdout,
                             r"(?m)^  tjs checklist --run +# every step")
            self.assertNotRegex(demo.stdout, r"(?m)^  tjs checklist +#")
            single = _tjs("init", "--single", "--country", "canada",
                          "--year", "2024", str(top / "single"), cwd=td)
            self.assertEqual(single.returncode, 0, single.stderr)
            self.assertRegex(single.stdout,
                             r"(?m)^  4\. tjs -C \S+ checklist --run$")
            multi = _tjs("init", "--country", "canada", "--year", "2024",
                         str(top / "multi"), cwd=td)
            self.assertEqual(multi.returncode, 0, multi.stderr)
            ny = _tjs("-C", str(top / "multi"), "new-year", "2025", cwd=td)
            self.assertEqual(ny.returncode, 0, ny.stderr)
            self.assertRegex(ny.stdout,
                             r"(?m)^  3\. tjs -C \S+ checklist --run$")
            for r in (single, ny):
                self.assertNotRegex(r.stdout, r"(?m)checklist$")
        guide = (REPO_ROOT / "docs" / "getting-started.md").read_text()
        step7 = guide.split("## 7. The filing checklist", 1)[1][:200]
        self.assertIn("tjs checklist --run\n```", step7)


class TestJsonSchemaVersions(unittest.TestCase):
    def test_tj0030_tj0045_forced_bumped_both_schema_versions(self):
        """TJ-0030, TJ-0045: v0.35 renamed the `skipped` status and mark
        to `forced` in `checklist --json` and `years --json` under the
        same schema_version (2 and 1), and settings.md still documented
        the years' marks as {"done", "skipped"}. Both versions are
        bumped and the docs say `forced`."""
        from taxjson.lib import project_layout as PL
        self.assertEqual(cl.SCHEMA_VERSION, 3)
        self.assertIn("forced", cl.STATUSES)
        self.assertNotIn("skipped", cl.STATUSES)
        with tempfile.TemporaryDirectory() as td:
            res = [cl.Result("install", "done")]
            doc = cl.to_json(res, 2025, "canada")
            self.assertEqual(doc["schema_version"], 3)
            self.assertIn("forced", doc["counts"])
            self.assertEqual(cl.guide_json()["schema_version"], 3)
            top = Path(td) / "multi"
            for y in (2024, 2025):
                (top / str(y)).mkdir(parents=True)
                (top / str(y) / "taxjson.toml").write_text(
                    f'[settings]\nyear = {y}\ncountry = "canada"\n')
            cl.set_override(top / "2024", 2024, "t5008", cl.MARK_FORCED)
            yrs = PL.years_report(top)
            self.assertEqual(yrs["schema_version"], 2)
            self.assertEqual(yrs["years"][0]["marks"],
                             {"done": 0, "forced": 1})
        settings = (REPO_ROOT / "docs" / "settings.md").read_text()
        years = settings.split("## `tjs years --json`", 1)[1]
        years = years.split("\n## ", 1)[0]
        self.assertIn('`marks` (`{"done", "forced"}`', years)
        self.assertNotIn('"skipped"', years)
        self.assertIn("while `schema_version` is 2", years)
        ck = settings.split("## `tjs checklist --json`", 1)[1]
        self.assertIn("`schema_version` is 3", ck.split("\n## ", 1)[0])
        upgrading = (REPO_ROOT / "docs" / "upgrading.md").read_text()
        self.assertIn("`schema_version` 3", upgrading.split(
            "### v0.35.0", 1)[0])


class TestSkipNamesItsReplacement(unittest.TestCase):
    def test_tj0042_skip_names_done_force(self):
        """TJ-0042: `checklist --skip ID` (gone in v0.35) failed with
        argparse's bare "unrecognized arguments: --skip t5008". It now
        stops naming `--done ID --force` (exit 2) and writes nothing,
        in a project and outside one; troubleshooting.md has the
        message."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            root.mkdir()
            _project(root)
            for where in (root, Path(td)):
                r = _tjs("-C", str(where), "checklist", "--skip", "t5008",
                         "--skip", "fx-cash", cwd=td)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertNotIn("unrecognized arguments", r.stderr)
                self.assertIn("--skip was replaced by --done --force: "
                              "`tjs checklist --done t5008,fx-cash "
                              "--force`", r.stderr)
                self.assertFalse((where / cl.STATE_FILE).exists())
            h = _tjs("checklist", "--help", cwd=td)
            self.assertNotIn("--skip", h.stdout)
        ts = (REPO_ROOT / "docs" / "troubleshooting.md").read_text()
        self.assertIn('"Error: unrecognized arguments: --skip ID"', ts)


class TestMarkConfirmations(unittest.TestCase):
    def test_tj0174_confirmations_name_no_program(self):
        """TJ-0174 (the part left open): `--done` / `--undo` / `--reset`
        confirmations started `tjs checklist: ...` (output-style: a
        line never starts with a program name). Now the step first, the
        later lines flush-left, within 120 columns piped."""
        from taxjson.lib import out
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root)
            ctx = _ctx(root, {})
            cl.save_results(ctx, [cl.Result("fx-cash", "attention",
                                            "FX on foreign cash: NOT "
                                            "RELIABLE for 2025")])
            runs = [_tjs("-C", str(root), "checklist", *a, cwd=td,
                         width=120)
                    for a in (("--done", "handoff"),
                              ("--done", "fx-cash", "--force"),
                              ("--done", "handoff", "--force"),
                              ("--undo", "handoff,t5008"),
                              ("--reset",), ("--reset",))]
            for r in runs:
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                self.assertTrue(r.stdout.strip(), r.stderr)
                for ln in r.stdout.splitlines():
                    self.assertFalse(ln.startswith(("taxjson", "tjs")), ln)
                    self.assertFalse(ln.startswith(" "), ln)
                    self.assertLessEqual(len(ln), 120, ln)
                self.assertEqual(out.lint(r.stdout, width_=120), [])
            self.assertTrue(runs[0].stdout.startswith(
                "handoff marked done (recorded in checklist.json)."))
            self.assertIn("\nThe check still says attention: shown [f]",
                          runs[1].stdout)
            self.assertIn("t5008 had no mark — nothing to undo.",
                          runs[3].stdout)
            self.assertEqual(runs[4].stdout, "checklist.json removed.\n")
            self.assertEqual(runs[5].stdout,
                             "No checklist.json — nothing to reset.\n")


class TestScopedClosingLine(unittest.TestCase):
    def test_tj0048_scoped_view_names_its_scope_and_the_next_elsewhere(self):
        """TJ-0048: `checklist --section 2` with nothing left in the
        section ended 'Nothing left to do now: the [?] items are yours
        to run and read.' with no [?] item in view and other sections
        still open. Now it says the scope, names the next open step
        elsewhere, and names [?] items only when the view has some."""
        sec = cl.SECTIONS[1]
        ids = cl.section_ids(sec)
        scoped = [cl.Result(sid, "done") for sid in ids]
        whole = [cl.Result(sid, "done") for sid in cl.item_ids()]
        whole = [cl.Result("missing-history", "attention", "1 position")
                 if r.id == "missing-history" else r for r in whole]
        label = cl.sections_label([sec])
        num = cl.item_ids().index("missing-history") + 1

        def last(text):
            return " ".join(text.strip().splitlines()[-1].split())
        text = cl.render(scoped, 2025, "canada", width_=0, scope=label,
                         whole=whole)
        self.assertEqual(last(text),
                         f"Nothing left to do in {label}; next (step {num},"
                         f" missing-history): `tjs find-missing-history`")
        self.assertNotIn("[?] items", last(text))
        text = cl.render(scoped, 2025, "canada", width_=0, scope=label)
        self.assertEqual(last(text), f"Nothing left to do in {label}.")
        summ = cl.render_summary(scoped, 2025, "canada", width_=0,
                                 scope=label, whole=whole)
        self.assertIn(f"Nothing left to do in {label}; next (step {num}",
                      last(summ))
        # The whole list: the [?] items named only when there are some.
        done = [cl.Result(sid, "done") for sid in cl.item_ids()]
        self.assertEqual(last(cl.render(done, 2025, "canada", width_=0)),
                         "Nothing left to do now.")
        rev = [cl.Result(r.id, "review") if r.id == "tips" else r
               for r in done]
        self.assertEqual(last(cl.render(rev, 2025, "canada", width_=0)),
                         "Nothing left to do now: the [?] items are yours "
                         "to run and read.")

    def test_tj0048_cli_section_view(self):
        """TJ-0048 through the command: kept verdicts with section 1
        done and a later step needing attention."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root)
            ctx = _ctx(root, {})
            cl.save_results(ctx, [cl.Result(sid, "done") for sid in
                                  cl.section_ids(cl.SECTIONS[0])]
                            + [cl.Result("missing-history", "attention",
                                         "1 position(s) with missing "
                                         "basis affect 2025")])
            r = _tjs("-C", str(root), "checklist", "--section", "1",
                     cwd=td)
            last = " ".join(r.stdout.strip().splitlines()[-1].split())
            self.assertTrue(last.startswith(
                "Nothing left to do in 1. Set up; next (step "), last)
            self.assertIn("missing-history", last)


class TestNotAGitRepository(unittest.TestCase):
    def test_tj0102_inputs_committed_names_git_init(self):
        """TJ-0102: outside any git repository the inputs-committed step
        said 'not a git repository — git status' (which fails there) and
        the closing line 'commit inputs/, taxjson.toml and ticker.map';
        nothing named `git init`. Now the step names `git init` (`git
        init ..` in a year folder of one folder of exports for every
        year), in the `! ` line and the closing line, without `git
        status`."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "single"
            root.mkdir()
            _project(root)
            r = cl.d_inputs_committed(_ctx(root, {}))
            self.assertEqual(r.status, "attention")
            self.assertTrue(r.detail.startswith("not a git repository"))
            self.assertIn("`git init`, then commit", r.detail)
            top = Path(td) / "multi"
            year = top / "2025"
            year.mkdir(parents=True)
            _project(year)
            (top / "inputs").mkdir()
            cfg = (year / "taxjson.toml").read_text().replace(
                "[settings]\n", '[settings]\ninputs_dir = "../inputs"\n', 1)
            (year / "taxjson.toml").write_text(cfg)
            ctx = _ctx(year, {})
            r = cl.d_inputs_committed(ctx)
            self.assertIn("`git init ..` (the folder above), then commit",
                          r.detail)
            cl.save_results(ctx, [r])
            p = _tjs("-C", str(year), "checklist", "--only",
                     "inputs-committed", cwd=td)
            text = p.stdout
            act = [ln for ln in text.splitlines()
                   if ln.startswith("! 20 inputs-committed")]
            self.assertEqual(act, ["! 20 inputs-committed: not a git "
                                   "repository — `git init ..` (the folder "
                                   "above), then commit"])
            self.assertLessEqual(len(act[0]), cl.ACT_WIDTH)
            last = text.strip().splitlines()[-1]
            self.assertEqual(last, "Next (step 20, inputs-committed): not a "
                             "git repository — `git init ..` (the folder "
                             "above), then commit")


class TestExportCoverageCount(unittest.TestCase):
    def test_tj0114_count_says_whose_rows_the_list_is_the_account(self):
        """TJ-0114: export-coverage said 'with 3 open position(s) —
        `tjs list margin 2025-06-02` lists them' and that list
        showed 5 (the account's .tt OPENING positions too): the count is
        the broker's rows', the list every source's. The text now says
        which is which."""
        from taxjson.lib import export_coverage as EC
        g = EC.Gap("margin", "questrade", "2025-06-02", "last-row",
                   "2025-12-31", False,
                   positions=[("ZZJ.TO", 10.0), ("ZZK.TO", 5.0)])
        text = EC.detail([g])
        self.assertIn("Questrade exports for margin end 2025-06-02 with 2 "
                      "open position(s) in Questrade's rows — `tjs "
                      "list margin 2025-06-02` lists the account's "
                      "positions on that day (every source)", text)
        self.assertNotIn("lists them", text)


class TestNoBooksBlockedLine(unittest.TestCase):
    def test_tj0178_fresh_project_one_blocked_line(self):
        """TJ-0178: a fresh project's checklist (no `tjs run` yet)
        had eleven differently worded blocked lines that did not group,
        several ending '— run `tjs run` — tjs run'. Every check
        blocked for the run now says 'no books yet' alike: one `! `
        line, the command said once."""
        with tempfile.TemporaryDirectory() as td:
            top = Path(td) / "fresh"
            r = _tjs("init", "--demo", str(top), cwd=td)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _tjs("-C", str(top / "2024"), "checklist", "--run", cwd=td)
            self.assertEqual(r.returncode, 1, r.stderr)
            acts = [ln for ln in r.stdout.splitlines() if ln.startswith("! ")]
            blocked = [ln for ln in acts if "blocked" in ln]
            self.assertEqual(len(blocked), 1, acts)
            self.assertRegex(blocked[0], r"^! \d+ blocked \(4 inputs-frozen, "
                             r"5 export-coverage \+\d+ more\): no books "
                             r"yet — tjs run$")
            for ln in acts:
                self.assertNotIn("`tjs run` — tjs run", ln)
            doc = json.loads(_tjs("-C", str(top / "2024"), "checklist",
                                  "--json", cwd=td).stdout)
            details = {s["detail"] for s in doc["steps"]
                       if s["effective"] == "blocked"}
            self.assertEqual(details, {cl.NO_BOOKS})

    def test_tj0178_act_line_says_the_command_once(self):
        """TJ-0178: a detail closing on "— run `tjs run` (first)"
        naming the line's own command loses it, as one closing on a bare
        "— `taxjson X`" did; another command's pointer stays."""
        self.assertEqual(
            cl._act_line("16 crypto-sends (blocked)", "no crypto transfer "
                         "evidence in work/ — run `tjs run`", "tjs run"),
            "! 16 crypto-sends (blocked): no crypto transfer evidence in "
            "work/ — tjs run")
        self.assertEqual(
            cl._act_line("28 audit (blocked)", "no computed books in work/ "
                         "— run `tjs run` first.", "tjs run"),
            "! 28 audit (blocked): no computed books in work/ — tjs run")
        self.assertEqual(
            cl._act_line("9 x", "stale — run `tjs sanity`", "tjs run"),
            "! 9 x: stale — run `tjs sanity` — tjs run")


class TestUsWording(unittest.TestCase):
    def test_tj0185_us_tobase_map_why_and_slip_aliases(self):
        """TJ-0185: a US project's tobase-map step said 'one ACB pool, one
        security for the superficial-loss rule' (Canadian law), and the
        slip steps could only be named t5008 / t5-t3. The US view has a
        US why; `--done 1099-b` / `1099-div` name the slip steps (the
        ids stay)."""
        us = {it.id: it for it in cl.items(2026, "usa")}
        ca = {it.id: it for it in cl.items(2026, "canada")}
        why = us["tobase-map"].why
        for word in ("ACB", "superficial", "TSX"):
            self.assertNotIn(word, why)
        self.assertIn("US-XLIST-05", why)
        self.assertIn("superficial-loss rule", ca["tobase-map"].why)
        self.assertIn("superficial-loss rule",
                      {it.id: it for it in cl.items(2026)}["tobase-map"].why)
        for alias, sid in (("1099-b", "t5008"), ("1099-DA", "t5008"),
                           ("1099-div", "t5-t3"), (" 1099-int ", "t5-t3")):
            self.assertEqual(cl.resolve_id(alias), sid)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "us"
            r = _tjs("init", "--single", "--country", "us", "--year",
                     "2026", str(root), cwd=td)
            self.assertEqual(r.returncode, 0, r.stderr)
            r = _tjs("-C", str(root), "checklist", "--done", "1099-b,1099-div",
                     cwd=td)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            marks = json.loads((root / cl.STATE_FILE).read_text())
            self.assertEqual(set(marks["overrides"]), {"t5008", "t5-t3"})


class TestInputsFrozenCutoff(unittest.TestCase):
    def test_tj0188_a_january_trade_before_the_cutoff(self):
        """TJ-0188: with a 2026-01-15 trade in the books inputs-frozen
        said 'January 2026 is not in the books yet'. It now names the
        cutoff the check uses: 'not through 2026-01-31 yet'."""
        from datetime import date
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root, activity_to="2026-01-15")
            r = cl.d_inputs_frozen(_ctx(root, {}, today=date(2026, 3, 1)))
        self.assertEqual(r.status, "attention")
        self.assertEqual(r.detail, "latest activity 2026-01-15 — not "
                         "through 2026-01-31 yet")
        self.assertNotIn("not in the books", r.detail)


if __name__ == "__main__":
    unittest.main()
