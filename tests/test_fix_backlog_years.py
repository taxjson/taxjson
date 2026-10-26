"""Backlog group `years`: `taxjson years` (lib/project_layout
.years_report, taxjson_run cmd_years / _years_table), `align` in a
filed year, and the checklist's year-end steps (inputs-frozen after
close-year, lock-committed, next-year). One test per backlog id, each
named for it. Synthetic data only."""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from taxjson.lib import checklist as CL
from taxjson.lib import project_layout as PL
from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent

_NOTE = ("## frozen by `taxjson close-year`: this year reads its own copy "
         "of the exports it was filed from")


def _env():
    return dict(os.environ, TAXJSON_OFFLINE="1", TAXJSON_WIDTH="0")


def _cli(root, *args):
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        cwd=REPO_ROOT, capture_output=True, text=True, env=_env(),
        stdin=subprocess.DEVNULL, timeout=300)


def _lock(year, *, closed_at=None, frozen=True, realized=100.0):
    doc = {"schema_version": 2, "year": year, "country": "canada",
           "basis": "wash-adjusted",
           "closed_at": closed_at or f"{year + 1}-03-01T12:00:00",
           "date_basis": "settle", "year_end": {}, "dispositions": [],
           "accounts": {"margin": {"realized": realized, "disallowed": 0.0,
                                   "dispositions": 1, "income": 0.0}},
           "totals": {"realized": realized, "disallowed": 0.0,
                      "dispositions": 1, "income": 0.0}}
    if frozen:
        doc["frozen"] = {"inputs_dir": "../inputs",
                         "tobase_map": "../tobase.map", "copied": 0,
                         "inputs": {}}
    return doc


def _years(top: Path, *, filed=True, newest_extra=""):
    """A folder of exports shared by every year: 2024 closed with its
    inputs frozen (the note, its own tobase.map and inputs/, the lock)
    — or, `filed` False, open and reading the shared folders — and
    2025 open, `newest_extra` added to its [settings]."""
    (top / "inputs" / "margin").mkdir(parents=True)
    (top / "tobase.map").write_text("# shared\n")
    y4 = top / "2024"
    (y4 / "inputs" / "margin").mkdir(parents=True)
    head = ('[settings]\nyear = {y}\ncountry = "canada"\n'
            'base_currency = "CAD"\nsource_currencies = []\n')
    tail = '\n[accounts.margin]\ntype = "taxable"\n'
    if filed:
        (y4 / "taxjson.toml").write_text(
            head.format(y=2024) + f'{_NOTE}\n# inputs_dir = "../inputs"\n'
            f'# tobase_map = "../tobase.map"\n' + tail)
        (y4 / "tobase.map").write_text("# shared\n")
        (y4 / "filed").mkdir()
        (y4 / "filed" / "2024.json").write_text(json.dumps(_lock(2024)))
    else:
        (y4 / "taxjson.toml").write_text(
            head.format(y=2024) + 'inputs_dir = "../inputs"\n'
            'tobase_map = "../tobase.map"\n' + tail)
    y5 = top / "2025"
    y5.mkdir()
    (y5 / "taxjson.toml").write_text(
        head.format(y=2025) + 'inputs_dir = "../inputs"\n'
        'tobase_map = "../tobase.map"\n'
        'prior_year_record = "../2024/filed/2024.json"\n'
        + newest_extra + tail)
    return y4, y5


def _acts(text):
    return [ln for ln in text.splitlines() if ln.startswith("! ")]


class TestYearsView(unittest.TestCase):
    def test_tj_0051_damaged_lock_line_cut_at_a_word_within_100(self):
        """TJ-0051: the damaged lock's `! ` line was the problem cut at
        80 characters mid-word ('not a finite numb'). It is now cut at a
        clause (or a word), ` ...` marking the cut, within 100 columns,
        the command still at its end."""
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            y4, _y5 = _years(top)
            lock = y4 / "filed" / "2024.json"
            doc = json.loads(lock.read_text())
            doc["accounts"]["margin"]["realized"] = float("nan")
            lock.write_text(json.dumps(doc))
            r = _cli(top, "years")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            line = next(a for a in _acts(r.stdout) if a.startswith("! 2024:"))
            self.assertLessEqual(len(line), 100, line)
            self.assertTrue(line.endswith(" — tjs years --details"), line)
            self.assertIn("realized as nan ...", line)
            self.assertNotIn("numb —", line)
        from taxjson.bin.taxjson_run import _years_act
        long = "2024: " + " ".join(["word"] * 40)
        line = _years_act(long, "tjs years --details")
        self.assertLessEqual(len(line), 100)
        self.assertRegex(line, r"^! 2024: (word )+\.\.\. — tjs years "
                               r"--details$")
        self.assertLessEqual(len(_years_act("2024:" + "x" * 150,
                                            "tjs years --details")), 100)
        short = _years_act("2024: filed/2024.json cannot be read",
                           "tjs years --details")
        self.assertEqual(short, "! 2024: filed/2024.json cannot be read — "
                                "tjs years --details")

    def test_tj_0116_single_folder_project_names_its_own_folder(self):
        """TJ-0116: in a single-folder project `years` headed its output
        with the parent folder's path; it names the project's folder."""
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "sgl"
            (proj / "inputs" / "margin").mkdir(parents=True)
            (proj / "taxjson.toml").write_text(
                '[settings]\nyear = 2024\ncountry = "canada"\n'
                'base_currency = "CAD"\n\n[accounts.margin]\n'
                'type = "taxable"\n')
            r = _cli(proj, "years")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            first = r.stdout.splitlines()[0]
            self.assertEqual(first, f"YEAR PROJECTS — {proj.resolve()}")
            self.assertIn("! This is a single-folder project, not a year "
                          "folder — tjs migrate --to-years", r.stdout)
            r = _cli(proj, "years", "--details")
            self.assertEqual(r.stdout.splitlines()[0],
                             f"Year projects in {proj.resolve()}")

    def test_tj_0103_filed_year_difference_is_its_own_not_a_step(self):
        """TJ-0103: a filed year whose settings differ from the newest
        year's was offered `years --diff` (and so align), which left a
        permanent "frozen input changed" note. The difference is shown
        as the filed year's own ('(filed)') with no step; an open
        older year that differs is still pointed at --diff."""
        with tempfile.TemporaryDirectory() as td:
            top = Path(td) / "f"
            _years(top, newest_extra='tax_date = "trade"\n')
            r = _cli(top, "years")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            row = next(ln for ln in r.stdout.splitlines()
                       if ln.startswith("2024 "))
            self.assertIn("1 setting(s) (filed)", row)
            self.assertNotIn("What differs", r.stdout)
            r = _cli(top, "years", "--details")
            self.assertIn("a filed year's own: `align` there would change",
                          " ".join(r.stdout.split()))
            top = Path(td) / "o"
            _years(top, filed=False, newest_extra='tax_date = "trade"\n')
            r = _cli(top, "years")
            self.assertIn("What differs: tjs years --diff 2024 2025",
                          r.stdout)
            self.assertNotIn("(filed)", r.stdout.split("\n\n")[1])

    def test_tj_0103_align_write_in_a_filed_year_warns(self):
        """TJ-0103: `align --from 2025 --write --all` in the filed 2024
        changed the settings the lock was closed with and said nothing;
        it now warns before writing (an open year: no warning)."""
        with tempfile.TemporaryDirectory() as td:
            top = Path(td) / "f"
            y4, _y5 = _years(top, newest_extra='tax_date = "trade"\n')
            r = _cli(y4, "align", "--from", "2025", "--write", "--all")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            flat = " ".join(r.stderr.split())
            self.assertIn("2024 is filed: --write changes the taxjson.toml "
                          "and ticker.map filed/2024.json was closed with",
                          flat)
            self.assertIn("close-year --force", flat)
            self.assertIn("taxjson.toml: 1 key(s) set", r.stdout)
            top = Path(td) / "o"
            y4, _y5 = _years(top, filed=False,
                             newest_extra='tax_date = "trade"\n')
            r = _cli(y4, "align", "--from", "2025", "--write", "--all")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("is filed", r.stderr)

    def test_tj_0158_unreadable_lock_keeps_the_year_frozen(self):
        """TJ-0158: with filed/2024.json unreadable, `years` offered
        `tjs migrate` for the frozen year's tobase.map (migrate then
        un-froze it) and counted its commented-out shared settings as
        differences. The freeze note in its taxjson.toml keeps it
        frozen while the lock is damaged."""
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            y4, _y5 = _years(top)
            (y4 / "filed" / "2024.json").write_text("{bad")
            self.assertTrue(PL.frozen_year(y4, 2024))
            self.assertEqual(sorted(PL.tobase_layout(top)["frozen"]), [2024])
            self.assertEqual(PL.tobase_layout(top)["own"], {})
            r = _cli(top, "years")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("tjs migrate", r.stdout)
            self.assertNotIn("What differs", r.stdout)
            self.assertIn("frozen copy of its own in 2024", r.stdout)
            self.assertIn("lock damaged", r.stdout)
            r = _cli(top, "migrate", "--dry-run")
            self.assertNotIn("2024/tobase.map", r.stdout + r.stderr)
            # Without the note (a year never frozen) a bad lock is not
            # taken as a freeze; nor is a missing lock.
            (y4 / "taxjson.toml").write_text(
                (y4 / "taxjson.toml").read_text().replace(_NOTE + "\n", ""))
            self.assertFalse(PL.frozen_year(y4, 2024))
            (y4 / "filed" / "2024.json").unlink()
            self.assertFalse(PL.frozen_year(y4, 2024))

    def test_tj_0159_years_shows_a_filed_year_that_drifted(self):
        """TJ-0159: `years` showed a filed year as clean while its last
        run said `filed 2024 DRIFTED`. The drift check's verdict is kept
        in work/filed_check.state and shown (STATE `DRIFTED`, an `! `
        line naming check-filed, `drifted` in --json) while the lock is
        the one judged."""
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            y4, _y5 = _years(top)
            (y4 / "work").mkdir()
            PL.record_filed_check(y4, {2024: "drifted"})
            self.assertTrue(PL.filed_check(y4, 2024))
            r = _cli(top, "years")
            row = next(ln for ln in r.stdout.splitlines()
                       if ln.startswith("2024 "))
            self.assertIn("DRIFTED", row)
            self.assertIn("! 2024: its last run DRIFTED from "
                          "filed/2024.json — tjs -C 2024 check-filed",
                          r.stdout)
            rep = json.loads(_cli(top, "years", "--json").stdout)
            self.assertEqual([y["drifted"] for y in rep["years"]],
                             [True, None])
            self.assertIn("DRIFTED from filed/2024.json",
                          " ".join(_cli(top, "years", "--details")
                                   .stdout.split()))
            # A clean check, or a lock closed again since: not drifted.
            PL.record_filed_check(y4, {2024: "ok"})
            self.assertIs(PL.filed_check(y4, 2024), False)
            PL.record_filed_check(y4, {2024: "drifted"})
            (y4 / "filed" / "2024.json").write_text(
                json.dumps(_lock(2024, realized=147.0)))
            self.assertIsNone(PL.filed_check(y4, 2024))
            self.assertNotIn("DRIFTED", _cli(top, "years").stdout)
            # A record that is not one is no verdict.
            (y4 / "work" / PL.FILED_CHECK_STATE).write_text('{"years": []}')
            self.assertIsNone(PL.filed_check(y4, 2024))

    def test_tj_0159_the_drift_check_records_its_verdicts(self):
        """TJ-0159: _check_filed_years (the run's and check-filed's
        drift check) keeps each judged year's verdict for `years`."""
        from taxjson.bin import taxjson_filed as TF
        from taxjson.bin import taxjson_run as TR
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            y4, _y5 = _years(top)
            (y4 / "work").mkdir()
            settings = TR.load_config(y4)["settings"]
            for diff, want in ((["margin realized 100.00 -> 147.40"], True),
                               ([], False)):
                with mock.patch.object(TF, "recompute_accounts",
                                       return_value={}), \
                        mock.patch.object(TF, "diff_snapshot",
                                          return_value=diff), \
                        contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    TR._check_filed_years(y4, y4 / "work", settings,
                                          strict=False)
                self.assertIs(PL.filed_check(y4, 2024), want)

    def test_tj_0193_years_json_keys_are_in_the_schema(self):
        """TJ-0193: `years --json` emitted `tobase_frozen`, which the
        schema in docs/settings.md did not list. Every key of a year
        (and of its `marks`) is documented there."""
        text = (REPO_ROOT / "docs" / "settings.md").read_text()
        sec = text.split("## `taxjson years --json`", 1)[1].split("\n## ", 1)[0]
        # A key in backticks, or quoted in a `{"a", "b"}` set.
        documented = set(re.findall(r"`([a-z_]+)`", sec)) | set(
            re.findall(r'"([a-z_]+)"', sec))
        with tempfile.TemporaryDirectory() as td:
            top = Path(td)
            _years(top)
            rep = PL.years_report(top)
        self.assertLessEqual(set(rep) - {"years"}, documented)
        for y in rep["years"]:
            self.assertLessEqual(set(y), documented, set(y) - documented)
            self.assertLessEqual(set(y["marks"]), documented)
        self.assertIn("tobase_frozen", documented)


def _ctx(root, cfg, year, today=date(2026, 9, 23)):
    return CL.Ctx(root=root, cfg=cfg, year=year, today=today,
                  run_sub=lambda argv, timeout=900: (0, "", ""))


_CFG = {"settings": {"year": 2025, "country": "canada",
                     "base_currency": "CAD"},
        "accounts": {"margin": {"type": "taxable"}}}


def _project(root: Path, latest="2025-12-16"):
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "m.csv").write_text("x\n")
    (root / "taxjson.toml").write_text(
        '[settings]\nyear = 2025\ncountry = "canada"\n'
        'base_currency = "CAD"\n\n[accounts.margin]\ntype = "taxable"\n')
    (root / "work").mkdir()
    rows = [{"action": "BUYSELL", "date": d, "date_settle": d,
             "symbol": "ZZQ.TO", "quantity": q, "account": "margin"}
            for d, q in (("2025-03-03", 10), (latest, -10))]
    (root / "work" / "margin_base.json").write_text(
        json.dumps({"transactions": rows}))


class TestYearEndSteps(unittest.TestCase):
    def test_tj_0142_next_year_in_a_single_folder_project(self):
        """TJ-0142: next-year named `tjs new-year 2025` (it refuses in a
        single-folder project), an init + align that failed, and a
        prior_year_record path that assumed the folder's name; never
        `migrate --to-years`. It names migrate --to-years, then
        new-year."""
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "proj"
            proj.mkdir()
            _project(proj)
            (proj / "filed").mkdir()
            (proj / "filed" / "2025.json").write_text(
                json.dumps(_lock(2025)))
            r = CL.s_next_year(_ctx(proj, _CFG, 2025), None)
            self.assertEqual(r.status, "review")
            self.assertIn("`tjs migrate --to-years`", r.detail)
            self.assertIn("`tjs new-year 2026`", r.detail)
            self.assertEqual(r.cmd, "tjs migrate --to-years")
            # A year folder: as before.
            y = Path(td) / "2025"
            y.mkdir()
            _project(y)
            (y / "filed").mkdir()
            (y / "filed" / "2025.json").write_text(json.dumps(_lock(2025)))
            self.assertEqual(CL.s_next_year(_ctx(y, _CFG, 2025), None).detail,
                             "start 2026")
        it = next(i for i in CL.items(2025, "canada") if i.id == "next-year")
        cmds = [c.command for c in it.cmds]
        self.assertIn("tjs migrate --to-years", cmds)
        self.assertFalse(any("init --single" in c for c in cmds), cmds)
        self.assertNotIn("prior_year_record =", it.how)
        self.assertIn("migrate --to-years", it.how)

    def test_tj_0143_inputs_frozen_after_close_year(self):
        """TJ-0143: once the year is filed, inputs-frozen was still the
        Next step, telling the user to download into inputs/<account>/,
        the frozen copy. It is done, the gap said with the amendment it
        would take; a lock taken before the year ended changes
        nothing."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root)
            ctx = _ctx(root, _CFG, 2025)
            self.assertEqual(CL.d_inputs_frozen(ctx).status, "attention")
            (root / "filed").mkdir()
            lock = root / "filed" / "2025.json"
            lock.write_text(json.dumps(_lock(2025)))
            r = CL.d_inputs_frozen(ctx)
            self.assertEqual(r.status, "done")
            self.assertIn("latest activity 2025-12-16", r.detail)
            self.assertIn("amendment", r.detail)
            lock.write_text(json.dumps(_lock(
                2025, closed_at="2025-12-20T10:00:00")))
            self.assertEqual(CL.d_inputs_frozen(ctx).status, "attention")

    def test_next_year_done_detail_names_checklist_run(self):
        """New backlog item "next-year done detail (like TJ-0011)": once
        the next year's folder is there, the step named `tjs checklist`
        (the last run's verdicts) where it means `checklist --run`."""
        with tempfile.TemporaryDirectory() as td:
            y = Path(td) / "2025"
            y.mkdir()
            _project(y)
            (Path(td) / "2026").mkdir()
            (Path(td) / "2026" / "taxjson.toml").write_text(
                '[settings]\nyear = 2026\ncountry = "canada"\n')
            r = CL.s_next_year(_ctx(y, _CFG, 2025), None)
            self.assertEqual(r.status, "done")
            self.assertIn("`tjs -C ../2026 checklist --run`", r.detail)

    def test_lock_committed_outside_git_names_git_init(self):
        """New backlog item "lock-committed outside git (like TJ-0102)":
        outside any git repository the step said only "not a git
        repository" and named `git status filed/`, which fails there.
        It names `git init` (in the folder holding the years when the
        year folders share one)."""
        with tempfile.TemporaryDirectory() as td:
            env = dict(os.environ, GIT_CEILING_DIRECTORIES=td)
            with mock.patch.dict(os.environ, env):
                root = Path(td) / "proj"
                root.mkdir()
                _project(root)
                (root / "filed").mkdir()
                (root / "filed" / "2025.json").write_text(
                    json.dumps(_lock(2025)))
                r = CL.d_lock_committed(_ctx(root, _CFG, 2025))
                self.assertEqual(r.status, "attention")
                self.assertIn("not a git repository — `git init`", r.detail)
                self.assertEqual(r.cmd, "git init")
                y4, _y5 = _years(Path(td) / "taxes")
                r = CL.d_lock_committed(_ctx(y4, _CFG, 2024))
                self.assertEqual(r.cmd, "git -C .. init")
                self.assertIn("then commit filed/2024.json", r.detail)

    @unittest.skipIf(shutil.which("git") is None, "git not installed")
    def test_tj_0147_lock_committed_sees_ignored_filed_reports(self):
        """TJ-0147: an older scaffold's unanchored `reports/` hides
        filed/<year>/reports/ from git, yet lock-committed said
        'committed'. It names the ignored path and the rule."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _project(root)
            (root / "filed" / "2025" / "reports").mkdir(parents=True)
            (root / "filed" / "2025.json").write_text(json.dumps(_lock(2025)))
            (root / "filed" / "2025" / "reports" / "margin.sum").write_text(
                "x\n")
            (root / ".gitignore").write_text("work/\nreports/\nexport/\n")

            def git(*a):
                subprocess.run(["git", "-c", "core.hooksPath=/dev/null",
                                "-C", str(root), *a], check=True,
                               capture_output=True,
                               stdin=subprocess.DEVNULL)
            git("init", "-q", ".")
            git("add", "-A")
            r = CL.d_lock_committed(_ctx(root, _CFG, 2025))
            self.assertEqual(r.status, "attention", r.detail)
            self.assertIn("filed/2025/reports/ is git-ignored", r.detail)
            self.assertIn("`reports/`", r.detail)
            self.assertIn("`/reports/`", r.detail)
            (root / ".gitignore").write_text("/work/\n/reports/\n/export/\n")
            git("add", "-A")
            r = CL.d_lock_committed(_ctx(root, _CFG, 2025))
            self.assertNotEqual(r.status, "attention", r.detail)


if __name__ == "__main__":
    unittest.main()
