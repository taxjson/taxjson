"""`taxjson compare` and close-year's freeze of the inputs.

- a full run keeps the reports it replaces as the `prev` snapshot;
  `compare` says "No difference" or names the figures, files and inputs
  that moved (exit 1), `--details` adds the diffs, `--json` the document;
- `--save` / `--list` / `--delete` keep snapshots by name;
- close-year copies the shared exports into the year's own inputs/,
  stops naming the shared folders, records the hashes in the lock and
  keeps the reports as filed: a later change to the shared folder moves
  nothing (`compare filed`, `check-filed` OK), a change to the frozen
  copy is named by check-filed; a stray local file refuses the close;
- the checklist's run-clean step stays clean after the freeze;
- new-year points the next year at the shared folders again;
- migrate --to-years gives a frozen single-folder year its own copy.

Synthetic data only.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_TOML = ('[settings]\nyear = 2024\ncountry = "canada"\n'
         'base_currency = "CAD"\nsource_currencies = []\n'
         'tax_date = "settle"\n')
_TT = ("BUYSELL 2024-03-01 09:30:00 ZZTEST.TO 100 CAD 10 -1000.00 0.00\n"
       "BUYSELL 2024-06-03 09:30:00 ZZTEST.TO -100 CAD 12 1200.00 0.00\n")
_MORE = ("BUYSELL 2024-07-02 09:30:00 ZZTEST.TO 50 CAD 11 -550.00 0.00\n"
         "BUYSELL 2024-08-01 09:30:00 ZZTEST.TO -50 CAD 9 450.00 0.00\n")
_TOBASE = "# the interlisted pairs (none in this test)\n"


def _cli(root, *args):
    env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL)


class _Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.top = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def years_project(self):
        """One folder of exports for every year: inputs/qt beside 2024/."""
        qt = self.top / "inputs" / "qt"
        qt.mkdir(parents=True)
        (qt / "trades.tt").write_text(_TT)
        (self.top / "tobase.map").write_text(_TOBASE)
        root = self.top / "2024"
        root.mkdir()
        (root / "taxjson.toml").write_text(
            _TOML + 'inputs_dir = "../inputs"\ntobase_map = "../tobase.map"\n'
            '\n[accounts.qt]\ntype = "taxable"\n')
        return root

    def single_project(self):
        root = self.top / "taxes"
        (root / "inputs" / "qt").mkdir(parents=True)
        (root / "inputs" / "qt" / "trades.tt").write_text(_TT)
        (root / "taxjson.toml").write_text(
            _TOML + '\n[accounts.qt]\ntype = "taxable"\n')
        return root

    def run_ok(self, root, *extra):
        r = _cli(root, "run", "--no-input", *extra)
        self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])
        return r

    def close_ok(self, root):
        r = _cli(root, "close-year", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])
        return r


class TestCompare(_Base):
    def test_prev_snapshot_then_a_change(self):
        root = self.years_project()
        r = _cli(root, "compare")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no reports/", r.stderr)
        self.run_ok(root)
        r = _cli(root, "compare")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no previous run kept yet", r.stderr)
        self.run_ok(root)
        r = _cli(root, "compare")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("No difference", r.stdout)
        self.assertIn("match the previous run", r.stdout)
        self.assertIn("Inputs:  unchanged", r.stdout)
        # A trade added to the shared export: the run keeps the old
        # reports, compare names the figure, the file and the input.
        with open(self.top / "inputs" / "qt" / "trades.tt", "a") as fh:
            fh.write(_MORE)
        self.run_ok(root)
        r = _cli(root, "compare")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("COMPARED reports/ with the previous run", r.stdout)
        self.assertIn("ACCOUNT  FIGURE", r.stdout)
        self.assertRegex(r.stdout, r"qt +dispositions +1 +2 +\+1")
        self.assertRegex(r.stdout, r"Reports: +\d+ of \d+ changed: .*qt\.sum")
        self.assertRegex(r.stdout, r"Inputs: +1 changed: inputs/qt/trades\.tt")
        self.assertIn("tjs compare --details", r.stdout)
        r = _cli(root, "compare", "--details")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--- prev/qt.sum", r.stdout)
        self.assertIn("+++ reports/qt.sum", r.stdout)
        self.assertNotIn("tjs compare --details", r.stdout)
        r = _cli(root, "compare", "--json")
        self.assertEqual(r.returncode, 1)
        doc = json.loads(r.stdout)
        self.assertTrue(doc["different"])
        figs = {(a["account"], a["figure"]): a for a in doc["accounts"]}
        self.assertEqual(figs[("qt", "dispositions")]["before"], 1)
        self.assertEqual(figs[("qt", "dispositions")]["after"], 2)
        self.assertEqual(doc["inputs"]["changed"], ["inputs/qt/trades.tt"])
        self.assertIn("qt.sum", doc["reports"]["changed"])
        self.assertIn("qt.sum", doc["diffs"])

    def test_save_list_delete(self):
        root = self.years_project()
        self.run_ok(root)
        r = _cli(root, "compare", "--list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No snapshot yet", r.stdout)
        r = _cli(root, "compare", "--save", "before-q4")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Saved", r.stdout)
        self.assertTrue((root / "work" / "compare" / "before-q4" / "reports"
                         / "qt.sum").is_file())
        r = _cli(root, "compare", "--list")
        self.assertRegex(r.stdout, r"NAME +KIND +TAKEN +REPORTS")
        self.assertRegex(r.stdout, r"before-q4 +saved +\d{4}-\d\d-\d\d")
        r = _cli(root, "compare", "before-q4")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("match snapshot before-q4", r.stdout)
        for bad in ("prev", "filed", "../x"):
            r = _cli(root, "compare", "--save", bad)
            self.assertEqual(r.returncode, 2, bad)
        r = _cli(root, "compare", "nope")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no snapshot named 'nope'", r.stderr)
        r = _cli(root, "compare", "--delete", "before-q4")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((root / "work" / "compare" / "before-q4").exists())
        r = _cli(root, "compare", "--json", "--list")
        self.assertEqual([s["name"] for s in json.loads(r.stdout)["snapshots"]],
                         [])

    def test_single_account_run_keeps_prev(self):
        root = self.years_project()
        self.run_ok(root)
        self.run_ok(root)
        self.run_ok(root, "--account", "qt")
        # Still the previous FULL run's reports, not a partial rotation.
        self.assertTrue((root / "work" / "compare" / "prev" / "reports"
                         / "qt.sum").is_file())


class TestFreeze(_Base):
    def test_close_year_freezes_the_inputs(self):
        root = self.years_project()
        self.run_ok(root)
        r = self.close_ok(root)
        self.assertRegex(r.stdout, r"Frozen: +inputs/ \(1 file\(s\) copied from "
                                   r"\.\./inputs, tobase\.map from "
                                   r"\.\./tobase\.map\); reports as filed: "
                                   r"filed/2024/reports/")
        self.assertEqual((root / "inputs" / "qt" / "trades.tt").read_text(),
                         _TT)
        self.assertEqual((root / "tobase.map").read_text(), _TOBASE)
        toml = (root / "taxjson.toml").read_text()
        self.assertIn("## frozen by `taxjson close-year`", toml)
        self.assertIn('# inputs_dir = "../inputs"', toml)
        self.assertIn('# tobase_map = "../tobase.map"', toml)
        from taxjson.lib import project_layout as PL
        st = PL.read_config(root)["settings"]
        self.assertNotIn("inputs_dir", st)
        self.assertNotIn("tobase_map", st)
        lock = json.loads((root / "filed" / "2024.json").read_text())
        fz = lock["frozen"]
        self.assertEqual(fz["inputs_dir"], "../inputs")
        self.assertEqual(fz["tobase_map"], "../tobase.map")
        self.assertEqual(fz["copied"], 1)
        self.assertIn("inputs/qt/trades.tt", fz["inputs"])
        self.assertIn("tobase.map", fz["inputs"])
        self.assertTrue((root / "filed" / "2024" / "reports" / "qt.sum")
                        .is_file())
        # The books are not stale for the checklist after the freeze.
        from taxjson.lib.checklist import inputs_changed
        self.assertEqual(inputs_changed(root, PL.read_config(root)), "")
        # The shared export moves on: the frozen year does not.
        with open(self.top / "inputs" / "qt" / "trades.tt", "a") as fh:
            fh.write(_MORE)
        r = self.run_ok(root)
        self.assertIn("filed 2024: OK", r.stdout + r.stderr)
        r = _cli(root, "compare", "filed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("match the reports as filed", r.stdout)
        self.assertIn("Inputs:  unchanged", r.stdout)
        r = _cli(root, "compare")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # The frozen copy itself edited: named by check-filed.
        with open(root / "inputs" / "qt" / "trades.tt", "a") as fh:
            fh.write(_MORE)
        self.run_ok(root)
        r = _cli(root, "check-filed")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("DRIFTED", r.stdout + r.stderr)
        self.assertRegex(r.stdout + r.stderr,
                         r"1 frozen input\(s\) changed since the year was "
                         r"closed: inputs/qt/trades\.tt")
        r = _cli(root, "compare", "filed")
        self.assertEqual(r.returncode, 1)
        self.assertRegex(r.stdout, r"qt +dispositions +1 +2 +\+1")

    def test_close_year_refuses_a_stray_local_file(self):
        root = self.years_project()
        self.run_ok(root)
        (root / "inputs" / "qt").mkdir(parents=True)
        (root / "inputs" / "qt" / "stray.csv").write_text("a,b\n")
        r = _cli(root, "close-year", "--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("inputs/qt/ already holds files this project does not "
                      "read", r.stderr)
        self.assertIn("Nothing was written.", r.stderr)
        self.assertFalse((root / "filed" / "2024.json").exists())
        self.assertFalse((root / "inputs" / "qt" / "trades.tt").exists())
        self.assertIn('inputs_dir = "../inputs"',
                      (root / "taxjson.toml").read_text())

    def test_force_reclose_keeps_the_record(self):
        root = self.years_project()
        self.run_ok(root)
        self.close_ok(root)
        r = _cli(root, "close-year", "--yes", "--force")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertRegex(r.stdout, r"Frozen: +the project's own inputs/ "
                                   r"\(frozen earlier from \.\./inputs\)")
        fz = json.loads((root / "filed" / "2024.json").read_text())["frozen"]
        self.assertEqual(fz["inputs_dir"], "../inputs")
        self.assertEqual(fz["tobase_map"], "../tobase.map")
        self.assertEqual(fz["copied"], 0)

    def test_new_year_reads_the_shared_folders_again(self):
        root = self.years_project()
        self.run_ok(root)
        self.close_ok(root)
        r = _cli(root, "new-year", "2025")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        from taxjson.lib import project_layout as PL
        st = PL.read_config(self.top / "2025")["settings"]
        self.assertEqual(st["inputs_dir"], "../inputs")
        self.assertEqual(st["tobase_map"], "../tobase.map")
        text = (self.top / "2025" / "taxjson.toml").read_text()
        self.assertNotIn("frozen by", text)
        self.assertFalse((self.top / "2025" / "tobase.map").exists())
        self.run_ok(self.top / "2025")

    def test_single_folder_close_records_and_migrate_keeps_a_copy(self):
        root = self.single_project()
        self.run_ok(root)
        r = self.close_ok(root)
        self.assertRegex(r.stdout, r"Frozen: +the project's own inputs/; "
                                   r"reports as filed: filed/2024/reports/")
        fz = json.loads((root / "filed" / "2024.json").read_text())["frozen"]
        self.assertIsNone(fz["inputs_dir"])
        self.assertEqual(fz["copied"], 0)
        self.assertIn("inputs/qt/trades.tt", fz["inputs"])
        r = _cli(root, "migrate", "--to-years")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("2024 is filed with its inputs frozen", r.stdout)
        year = root / "2024"
        self.assertEqual((year / "inputs" / "qt" / "trades.tt").read_text(),
                         _TT)
        self.assertEqual((root / "inputs" / "qt" / "trades.tt").read_text(),
                         _TT)
        from taxjson.lib import project_layout as PL
        st = PL.read_config(year)["settings"]
        self.assertNotIn("inputs_dir", st)
        self.assertEqual(st["exports_dir"], "../exports")
        r = self.run_ok(year)
        self.assertIn("filed 2024: OK", r.stdout + r.stderr)
        r = _cli(year, "compare", "filed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        # The next year reads the shared folder beside the year folders.
        r = _cli(year, "new-year", "2025")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(PL.read_config(root / "2025")["settings"]
                         ["inputs_dir"], "../inputs")


if __name__ == "__main__":
    unittest.main()


class TestFreezeAudit(_Base):
    """2026-10 workflow audit: a frozen year's own tobase.map is the
    freeze, never a copy for `migrate` to merge; one freeze note; the
    scaffold .gitignore lets filed/<year>/reports/ be committed."""

    def test_years_and_migrate_leave_the_frozen_tobase_map_alone(self):
        root = self.years_project()
        self.run_ok(root)
        self.close_ok(root)
        r = _cli(self.top, "years")
        self.assertIn("tobase.map: frozen copy of its own in 2024 (filed)",
                      r.stdout)
        self.assertNotIn("tjs migrate", r.stdout)
        r = _cli(root, "new-year", "2025")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        r = _cli(self.top, "years")
        self.assertNotIn("tjs migrate", r.stdout)
        for where in (root, self.top):
            r = _cli(where, "migrate")
            self.assertIn("nothing to migrate", r.stdout + r.stderr)
            self.assertEqual((root / "tobase.map").read_text(), _TOBASE)
        text = (root / "taxjson.toml").read_text()
        # The keys are adjacent: one note reads over both (polish pass).
        self.assertEqual(text.count("## frozen by `taxjson close-year`"), 1)
        self.assertNotIn("## frozen by `taxjson close-year` too\n", text)
        self.assertEqual(text.count("tobase_map"), 1)   # commented once
        from taxjson.lib import project_layout as PL
        lay = PL.tobase_layout(self.top)
        self.assertEqual(list(lay["frozen"]), [2024])
        self.assertEqual(lay["own"], {})
        # update-tobase-map from the top no longer sees an ambiguity.
        r = _cli(self.top, "update-tobase-map")
        self.assertNotIn("ambiguous", r.stdout + r.stderr)

    def test_scaffold_gitignore_keeps_the_filed_reports(self):
        import shutil
        if not shutil.which("git"):
            self.skipTest("no git")
        r = _cli(self.top / "demo", "init", "--demo", str(self.top / "demo"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        top = self.top / "demo"
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")
        subprocess.run(["git", "init", "-q"], cwd=top, check=True, env=env)

        def ignored(rel):
            return subprocess.run(["git", "check-ignore", "-q", rel],
                                  cwd=top, env=env).returncode == 0
        self.assertTrue(ignored("2024/work/x"))
        self.assertTrue(ignored("2024/reports/x.sum"))
        self.assertTrue(ignored("exports/x"))
        self.assertFalse(ignored("2024/filed/2024/reports/x.sum"))
        self.assertFalse(ignored("2024/filed/2024.json"))
        # An older scaffold's unanchored rule: close-year says so.
        (top / ".gitignore").write_text("work/\nreports/\n")
        self.run_ok(top / "2024")
        r = self.close_ok(top / "2024")
        self.assertIn("`reports/` also ignores filed/2024/reports/",
                      " ".join(r.stdout.split()))
