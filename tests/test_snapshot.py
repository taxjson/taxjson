"""`tjs snapshot`: the whole project copied to
snapshot/<year>.<timestamp>/, laid out as it is.

- a single project's copy is at the snapshot's top; a year folder's is
  under its own name beside the shared folders it reads, so `diff -r`
  matches file by file and taxjson runs in the copy;
- `compare NAME` reads it, `--list` names this year's, `--delete` removes
  it; another year's is refused;
- no snapshot is replaced; a failed one leaves nothing behind;
- a repository, work/compare/ and the snapshots folder are not copied.

Synthetic data only.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_TOML = ('[settings]\nyear = 2024\ncountry = "canada"\n'
         'base_currency = "CAD"\nsource_currencies = []\n'
         'tax_date = "settle"\n')
_ACCOUNT = '\n[accounts.qt]\ntype = "taxable"\n'
_TT = ("BUYSELL 2024-03-01 09:30:00 ZZTEST.TO 100 CAD 10 -1000.00 0.00\n"
       "BUYSELL 2024-06-03 09:30:00 ZZTEST.TO -100 CAD 12 1200.00 0.00\n")
_MORE = ("BUYSELL 2024-07-02 09:30:00 ZZTEST.TO 50 CAD 11 -550.00 0.00\n"
         "BUYSELL 2024-08-01 09:30:00 ZZTEST.TO -50 CAD 9 450.00 0.00\n")


def _cli(root, *args):
    env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1",
               TAXJSON_WIDTH="0")
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", str(root),
         *args], cwd=REPO_ROOT, capture_output=True, text=True, env=env,
        stdin=subprocess.DEVNULL)


def _tree(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in Path(folder).rglob("*") if p.is_file()}


class _Base(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.top = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def single(self, extra=""):
        root = self.top / "taxes"
        (root / "inputs" / "qt").mkdir(parents=True)
        (root / "inputs" / "qt" / "trades.tt").write_text(_TT)
        (root / "taxjson.toml").write_text(_TOML + extra + _ACCOUNT)
        self.run_ok(root)
        return root

    def years(self):
        qt = self.top / "inputs" / "qt"
        qt.mkdir(parents=True)
        (qt / "trades.tt").write_text(_TT)
        (self.top / "tobase.map").write_text("# none in this test\n")
        (self.top / "notes.txt").write_text("not the project's\n")
        root = self.top / "2024"
        root.mkdir()
        (root / "taxjson.toml").write_text(
            _TOML + 'inputs_dir = "../inputs"\ntobase_map = "../tobase.map"\n'
            + _ACCOUNT)
        self.run_ok(root)
        return root

    def run_ok(self, root):
        r = _cli(root, "run", "--no-input")
        self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])

    def snap(self, root):
        r = _cli(root, "snapshot", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout)


class TestLayout(_Base):
    def test_single_project_is_mirrored_at_the_top(self):
        root = self.single()
        doc = self.snap(root)
        d = root / "snapshot" / doc["name"]
        self.assertRegex(doc["name"], r"^2024\.\d{8}-\d{6}$")
        self.assertEqual(_tree(d / "reports"), _tree(root / "reports"))
        self.assertEqual(_tree(d / "inputs"), _tree(root / "inputs"))
        self.assertEqual((d / "taxjson.toml").read_bytes(),
                         (root / "taxjson.toml").read_bytes())
        self.assertTrue((d / "work" / "qt_gains.json").is_file())
        self.assertEqual(doc["reports_folder"],
                         f"snapshot/{doc['name']}/reports/")

    def test_year_folder_is_mirrored_beside_what_it_reads(self):
        root = self.years()
        doc = self.snap(root)
        d = self.top / "snapshot" / doc["name"]
        self.assertEqual(_tree(d / "2024" / "reports"),
                         _tree(root / "reports"))
        self.assertEqual(_tree(d / "inputs"), _tree(self.top / "inputs"))
        self.assertTrue((d / "tobase.map").is_file())
        # What no setting names, beside the years, is not the project's.
        self.assertFalse((d / "notes.txt").exists())
        self.assertEqual(doc["reports_folder"],
                         f"../snapshot/{doc['name']}/2024/reports/")

    def test_other_years_locks_come_along_their_books_do_not(self):
        root = self.years()
        other = self.top / "2023"
        (other / "filed").mkdir(parents=True)
        (other / "filed" / "2023.json").write_text("{}\n")
        (other / "work").mkdir()
        (other / "work" / "x.json").write_text("{}\n")
        d = self.top / "snapshot" / self.snap(root)["name"]
        self.assertTrue((d / "2023" / "filed" / "2023.json").is_file())
        self.assertFalse((d / "2023" / "work").exists())

    def test_taxjson_runs_in_the_copy_and_rebuilds_the_same(self):
        root = self.years()
        doc = self.snap(root)
        copy = self.top / "snapshot" / doc["name"] / "2024"
        self.run_ok(copy)
        r = _cli(copy, "compare", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(json.loads(r.stdout)["inputs"]["changed"])
        self.assertEqual((copy / "reports" / "qt.sum").read_bytes(),
                         (root / "reports" / "qt.sum").read_bytes())

    def test_what_is_left_out(self):
        root = self.single()
        (root / ".git").mkdir()
        (root / ".git" / "HEAD").write_text("ref\n")
        (root / "inputs_redact").mkdir()
        (root / "inputs_redact" / "a.csv").write_text("x\n")
        (root / "work" / "compare" / "saved").mkdir(parents=True)
        first = self.snap(root)
        d = root / "snapshot" / self.snap(root)["name"]
        self.assertFalse((d / ".git").exists())
        self.assertFalse((d / "inputs_redact").exists())
        self.assertFalse((d / "work" / "compare").exists())
        # The earlier snapshot is not copied into the next.
        self.assertFalse((d / "snapshot").exists())
        self.assertTrue((root / "snapshot" / first["name"]).is_dir())

    def test_a_link_leaving_the_copy_is_named_not_followed(self):
        root = self.single()
        outside = self.top / "outside.txt"
        outside.write_text("secret\n")
        (root / "inputs" / "qt" / "link.txt").symlink_to(outside)
        r = _cli(root, "snapshot", "--json")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["links_leaving"], ["inputs/qt/link.txt"])
        self.assertIn("inputs/qt/link.txt", r.stderr)
        self.assertTrue((root / "snapshot" / doc["name"] / "inputs" / "qt"
                         / "link.txt").is_symlink())

    def test_a_closed_year_keeps_the_same_snapshots_folder(self):
        from taxjson.lib.project_layout import snapshots_folder
        root = self.years()
        self.assertEqual(snapshots_folder(root), root.parent / "snapshot")
        (root / "taxjson.toml").write_text(_TOML + _ACCOUNT)   # no shared
        self.assertEqual(snapshots_folder(root), root.parent / "snapshot")


class TestCompare(_Base):
    def test_compare_reads_it_and_names_what_moved(self):
        root = self.single()
        name = self.snap(root)["name"]
        r = _cli(root, "compare", name)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("No difference", r.stdout)
        with (root / "inputs" / "qt" / "trades.tt").open("a") as f:
            f.write(_MORE)
        self.run_ok(root)
        r = _cli(root, "compare", name, "--json")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        doc = json.loads(r.stdout)
        self.assertEqual(doc["kind"], "snapshot")
        self.assertIn("realized", {x["figure"] for x in doc["accounts"]})
        self.assertEqual(doc["inputs"]["changed"], ["inputs/qt/trades.tt"])

    def test_a_folder_reads_like_its_name(self):
        root = self.years()
        name = self.snap(root)["name"]
        for given in (f"../snapshot/{name}", f"../snapshot/{name}/",
                      str(self.top / "snapshot" / name)):
            r = _cli(root, "compare", given, "--json")
            self.assertEqual(r.returncode, 0, given + r.stdout + r.stderr)
            doc = json.loads(r.stdout)
            self.assertEqual(doc["against"], name)
            self.assertEqual(doc["kind"], "snapshot")

    def test_a_folder_is_found_from_the_shells_folder_first(self):
        root = self.years()
        name = self.snap(root)["name"]
        env = dict(os.environ, TAXJSON_OFFLINE="1", NO_COLOR="1",
                   TAXJSON_WIDTH="0",
                   PYTHONPATH=os.pathsep.join(
                       [str(REPO_ROOT / "src"),
                        os.environ.get("PYTHONPATH", "")]))
        r = subprocess.run(
            [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C", "2024",
             "compare", f"snapshot/{name}"], cwd=self.top,
            capture_output=True, text=True, env=env,
            stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("No difference", r.stdout)

    def test_a_folder_moved_to_a_backup_still_reads(self):
        root = self.years()
        name = self.snap(root)["name"]
        backup = self.top / "backup"
        backup.mkdir()
        (self.top / "snapshot" / name).rename(backup / name)
        r = _cli(root, "compare", str(backup / name))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"snapshot {name}", r.stdout)

    def test_a_folder_that_is_no_snapshot_is_refused(self):
        root = self.single()
        r = _cli(root, "compare", "./reports")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("is not a snapshot", r.stderr)
        r = _cli(root, "compare", "../nowhere/2024.1")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("no snapshot at ../nowhere/2024.1", r.stderr)

    def test_delete_takes_a_name_only(self):
        root = self.years()
        name = self.snap(root)["name"]
        r = _cli(root, "compare", "--delete", f"../snapshot/{name}")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertTrue((self.top / "snapshot" / name).is_dir())

    def test_list_and_delete(self):
        root = self.years()
        name = self.snap(root)["name"]
        items = json.loads(_cli(root, "compare", "--list", "--json").stdout)
        self.assertIn((name, "snapshot"),
                      [(i["name"], i["kind"])
                       for i in items["snapshots"]])
        r = _cli(root, "compare", "--delete", name)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.top / "snapshot" / name).exists())
        self.assertEqual(_cli(root, "compare", name).returncode, 2)

    def test_another_years_snapshot_is_refused(self):
        root = self.years()
        name = self.snap(root)["name"]
        other = self.top / "2025"
        other.mkdir()
        (other / "taxjson.toml").write_text(
            _TOML.replace("2024", "2025")
            + 'inputs_dir = "../inputs"\ntobase_map = "../tobase.map"\n'
            + _ACCOUNT)
        self.run_ok(other)
        items = json.loads(_cli(other, "compare", "--list", "--json").stdout)
        self.assertNotIn(name, [i["name"] for i in items["snapshots"]])
        r = _cli(other, "compare", name)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("a snapshot of 2024", r.stderr)

    def test_a_damaged_project_field_cannot_lead_elsewhere(self):
        root = self.single()
        name = self.snap(root)["name"]
        f = root / "snapshot" / name / "snapshot.json"
        doc = json.loads(f.read_text())
        doc["project"] = "../.."
        f.write_text(json.dumps(doc))
        r = _cli(root, "compare", name)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("project", r.stderr)
        self.assertIn(f"snapshot/{name}/snapshot.json", r.stderr)


class TestRefusals(_Base):
    def test_no_reports(self):
        root = self.top / "taxes"
        (root / "inputs" / "qt").mkdir(parents=True)
        (root / "taxjson.toml").write_text(_TOML + _ACCOUNT)
        r = _cli(root, "snapshot")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("run `tjs run` first", r.stderr)
        self.assertFalse((root / "snapshot").exists())

    def test_a_link_for_the_folder_is_refused(self):
        root = self.single()
        (self.top / "elsewhere").mkdir()
        (root / "snapshot").symlink_to(self.top / "elsewhere")
        r = _cli(root, "snapshot")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("is a link", r.stderr)
        self.assertEqual(list((self.top / "elsewhere").iterdir()), [])

    def test_a_year_folders_holdings_named_snapshot_is_refused(self):
        qt = self.top / "inputs" / "qt"
        qt.mkdir(parents=True)
        (qt / "trades.tt").write_text(_TT)
        (self.top / "snapshot").mkdir()
        root = self.top / "2024"
        root.mkdir()
        (root / "taxjson.toml").write_text(
            _TOML + 'inputs_dir = "../inputs"\n'
            'holdings_dir = "../snapshot"\n' + _ACCOUNT)
        self.run_ok(root)
        r = _cli(root, "snapshot")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("a folder this project reads", r.stderr)

    def test_never_replaces_one_and_leaves_nothing_when_it_fails(self):
        from taxjson.lib import compare as C
        from taxjson.bin.taxjson_run import load_config
        root = self.single()
        cfg = load_config(root)
        now = datetime(2026, 1, 2, 3, 4, 5)
        a = C.take_full(root, cfg, now=now)
        b = C.take_full(root, cfg, now=now)
        self.assertEqual(a["name"], "2024.20260102-030405")
        self.assertEqual(b["name"], "2024.20260102-030405-2")
        os.mkfifo(root / "inputs" / "qt" / "pipe")
        with self.assertRaises(C.CompareError) as e:
            C.take_full(root, cfg, now=now)
        self.assertIn("cannot be made", str(e.exception))
        self.assertEqual(sorted(p.name for p in (root / "snapshot").iterdir()),
                         [a["name"], b["name"]])


class TestScaffold(unittest.TestCase):
    def test_the_gitignore_keeps_the_copies_out(self):
        from taxjson.bin.taxjson_run import _TEMPLATE_GITIGNORE
        self.assertIn("\n/snapshot/\n/*/snapshot/\n", _TEMPLATE_GITIGNORE)


if __name__ == "__main__":
    unittest.main()
