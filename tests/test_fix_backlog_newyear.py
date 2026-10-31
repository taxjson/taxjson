"""Backlog group `newyear`: `tjs new-year`, `tjs migrate` and a
filed year's frozen settings. One test per finding, each named for its
backlog id (TJ-NNNN). Synthetic data only (made-up tickers, .tt lines).
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv

REPO_ROOT = Path(__file__).resolve().parent.parent

_TT = """\
# synthetic
BUYSELL 2023-02-01 10:00:00 QZQ.TO 100 CAD 20.00 2009.95 9.95
BUYSELL 2023-06-01 10:00:00 QZQ.TO -50 CAD 25.00 1240.05 9.95
BUYSELL 2024-03-01 10:00:00 ZZB.TO 40 CAD 10.00 409.95 9.95
BUYSELL 2024-12-16 10:00:00 ZZB.TO -40 CAD 8.00 310.05 9.95
"""


def _env(width="0"):
    e = dict(os.environ, TAXJSON_OFFLINE="1")
    if width is None:
        e.pop("TAXJSON_WIDTH", None)        # piped: lib/out.WIDTH (120)
    else:
        e["TAXJSON_WIDTH"] = width
    return e


def _cli(root, *args, width="0"):
    return subprocess.run(
        python_module_argv("taxjson.bin.taxjson_run",
                           ["-C", str(root), *args]),
        cwd=REPO_ROOT, capture_output=True, text=True, env=_env(width),
        stdin=subprocess.DEVNULL, timeout=600)


def _toml(year, shared=True, extra=""):
    return ("[settings]\n"
            f"year = {year}\n"
            'country = "canada"\n'
            + ('inputs_dir = "../inputs"\n' if shared else "")
            + 'tax_date = "settle"\n'
            'base_currency = "CAD"\n'
            "option_grant_timing_since = 2023\n"
            f"{extra}\n"
            "[accounts.margin]\n"
            'type = "taxable"\n')


def _years(td, years):
    """A Canadian multi-year project: the exports in inputs/ beside the
    year folders, each year a project reading them."""
    top = Path(td) / "taxes"
    (top / "inputs" / "margin").mkdir(parents=True)
    (top / "inputs" / "margin" / "trades.tt").write_text(_TT)
    for y in years:
        (top / str(y)).mkdir()
        (top / str(y) / "taxjson.toml").write_text(_toml(y))
    return top


def _single(td, year):
    """A Canadian single-folder project (its own inputs/)."""
    root = Path(td) / "p"
    (root / "inputs" / "margin").mkdir(parents=True)
    (root / "inputs" / "margin" / "trades.tt").write_text(_TT)
    (root / "taxjson.toml").write_text(_toml(year, shared=False))
    return root


class TestNewYearGap(unittest.TestCase):
    def test_tj0036_new_year_refuses_when_the_year_before_has_no_folder(self):
        """TJ-0036: `new-year 2026` with 2024 the newest folder started
        2026 from 2024 (prior_year_record at 2024's lock), skipping
        whatever 2025 used. Now it refuses, naming the year to start
        first, and creates nothing."""
        with tempfile.TemporaryDirectory() as td:
            top = _years(td, (2023, 2024))
            r = _cli(top, "new-year", "2026")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("2025 has no year folder: start it first",
                          r.stderr)
            self.assertIn("`tjs new-year 2025`", r.stderr)
            self.assertFalse((top / "2026").exists())
            # The year before, then the year: both made.
            r = _cli(top, "new-year", "2025")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = _cli(top, "new-year", "2026")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn('"../2025/filed/2025.json"',
                          (top / "2026" / "taxjson.toml").read_text())


def _ok(tc, r):
    tc.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-3000:])
    return r


def _long_lines(text, width=120):
    return [ln for ln in text.splitlines() if len(ln) > width]


class TestFrozenYearMigrated(unittest.TestCase):
    """A single-folder 2024 project closed with its inputs frozen, then
    `migrate --to-years` (TJ-0049, TJ-0190) and the frozen year's
    settings named again (TJ-0108)."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        root = _single(cls._td.name, 2024)
        (root / "tobase.map").write_text("TOBASE QZA.US QZA.TO\n")
        for args in (("format", "--write", "--no-backup"),
                     ("run", "--no-input"), ("close-year", "--yes")):
            r = _cli(root, *args)
            assert r.returncode == 0, (args, r.stdout[-2000:],
                                       r.stderr[-2000:])
        cls.root = root
        cls.migrate = _cli(root, "migrate", "--to-years", width=None)
        cls.year = root / "2024"

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_tj0049_migrate_to_years_keeps_the_template_layout(self):
        """TJ-0049: the keys migrate sets (exports_dir; the frozen year
        reads its own inputs) were appended unaligned at the end of
        [settings], the template's line still commented above: `format
        --check` failed right after the migration."""
        _ok(self, self.migrate)
        text = (self.year / "taxjson.toml").read_text()
        self.assertRegex(text, r'(?m)^exports_dir +=  ?"\.\./exports"$')
        self.assertNotIn('# exports_dir', text)
        self.assertNotRegex(text, r'(?m)^exports_dir = ')
        r = _ok(self, _cli(self.year, "format", "--check"))
        self.assertIn("already formatted", r.stdout)

    def test_tj0190_migrate_to_years_lines_wrap_at_120(self):
        """TJ-0190: piped, the frozen year's line (156 columns) and the
        closing `Done.` line (204) were printed whole."""
        out = self.migrate.stdout
        self.assertIn("2024 is filed with its inputs frozen", out)
        self.assertIn("Done.", out)
        self.assertEqual(_long_lines(out), [], out)

    def test_tj0108_frozen_year_naming_the_shared_inputs_again_warns(self):
        """TJ-0108: a filed, frozen year whose [settings] names the
        shared inputs_dir again got an `Info:` and 'OK' (exit 0, also
        under --strict) and `tjs years` showed no problem. Now a
        `Warning:`, `run --strict` stops, and the year's row names it."""
        _ok(self, self.migrate)
        cfg = self.year / "taxjson.toml"
        before = cfg.read_text()
        self.addCleanup(cfg.write_text, before)
        cfg.write_text(before.replace(
            "[settings]\n", '[settings]\ninputs_dir = "../inputs"\n', 1))
        r = _cli(self.year, "check-filed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = r.stdout + r.stderr
        self.assertIn("warning: filed 2024 was closed with its inputs "
                      "frozen, but taxjson.toml names the shared "
                      "inputs_dir (../inputs) again", out)
        self.assertNotIn("note: filed 2024 was closed", out)
        r = _cli(self.year, "run", "--no-input", "--strict")
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("1 filed year(s) no longer read their frozen inputs",
                      r.stdout + r.stderr)
        import json
        r = _ok(self, _cli(self.root, "years", "--json"))
        rec = {y["year"]: y for y in json.loads(r.stdout)["years"]}[2024]
        self.assertEqual(rec["problem"], "frozen, but [settings] names "
                                         "inputs_dir again: delete it")
        self.assertTrue(rec["tobase_frozen"])
        self.assertFalse(rec["tobase_shared"])


class TestEarlierYearsLock(unittest.TestCase):
    def test_tj0087_an_earlier_years_frozen_lock_is_not_told_to_unshare(
            self):
        """TJ-0087: a single-folder 2023 project closed (inputs frozen),
        gone on to 2024, then `migrate --to-years`: 2024/filed/2023.json
        is an earlier year's lock and 2024 rightly reads the shared
        inputs — yet the run and check-filed said to delete inputs_dir
        (which empties the books). Also TJ-0049 on the unfrozen path:
        inputs_dir and exports_dir go in their places."""
        with tempfile.TemporaryDirectory() as td:
            root = _single(td, 2023)
            for args in (("format", "--write", "--no-backup"),
                         ("run", "--no-input"), ("close-year", "--yes")):
                _ok(self, _cli(root, *args))
            cfg = root / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace(
                "= 2023\n", "= 2024\n", 1))
            _ok(self, _cli(root, "format", "--write", "--no-backup"))
            r = _ok(self, _cli(root, "migrate", "--to-years", width=None))
            self.assertEqual(_long_lines(r.stdout), [], r.stdout)
            year = root / "2024"
            self.assertIn("already formatted",
                          _ok(self, _cli(year, "format", "--check")).stdout)
            for args in (("run", "--no-input"), ("check-filed",)):
                r = _ok(self, _cli(year, *args))
                out = r.stdout + r.stderr
                self.assertNotIn("delete that line", out)
                self.assertNotIn("restore them from your records", out)
                self.assertIn("filed 2023: OK", out)
            self.assertIn("filed 2023 is checked from the inputs this 2024 "
                          "project reads", out)


def _fake_frozen_lock(d, year):
    """A lock with a `frozen` record (project_layout.frozen_year reads
    nothing else)."""
    (d / "filed").mkdir(exist_ok=True)
    (d / "filed" / f"{year}.json").write_text(
        '{"year": %d, "frozen": {"inputs_dir": "../inputs", '
        '"tobase_map": "../tobase.map", "copied": 1, "inputs": {}}}' % year)


class TestMigrateSharedTobase(unittest.TestCase):
    def test_tj0086_a_frozen_years_own_copy_is_not_told_to_share(self):
        """TJ-0086: migrate listed the filed, frozen 2024 as reading no
        tobase.map and told the user to add the setting there (the drift
        path of the 2026-10-17 item 1), and said `set ...` while it then
        refused for differing copies."""
        with tempfile.TemporaryDirectory() as td:
            top = _years(td, (2024, 2025))
            (top / "tobase.map").write_text("TOBASE QZA.US QZA.TO\n")
            (top / "2024" / "tobase.map").write_text(
                "TOBASE QZA.US QZA.TO\n")
            _fake_frozen_lock(top / "2024", 2024)
            (top / "2025" / "tobase.map").write_text(
                "TOBASE QZA.US QZA.TO\n# mine\n")
            for args in (("migrate", "--dry-run"), ("migrate",)):
                r = _cli(top, *args)
                out = r.stdout + r.stderr
                self.assertIn("Not changed: 2024 reads its own frozen copy",
                              out, args)
                self.assertNotIn("read no tobase.map", out, args)
                self.assertNotIn("add the setting there", out, args)
                self.assertIn("would set [settings] tobase_map", out, args)
                self.assertNotRegex(out, r"(?m)^  set \[settings\]")
            self.assertEqual(r.returncode, 1, out)
            self.assertIn("copies differ", r.stderr)

    def test_tj0050_a_refused_copy_is_never_the_one_kept(self):
        """TJ-0050: after refusing a non-UTF-8 tobase.map, migrate went
        on to print the plan naming that file 'the one kept'; with the
        older copy the one refused, the error had no byte or offset."""
        for bad in (2024, 2023):
            with tempfile.TemporaryDirectory() as td:
                top = _years(td, (2023, 2024))
                for y in (2023, 2024):
                    (top / str(y) / "tobase.map").write_bytes(
                        b"TOBASE QZA.US QZA.TO\n")
                with open(top / str(bad) / "tobase.map", "ab") as f:
                    f.write(b"TOBASE QZB.US QZB.TO # caf\xe9\n")
                r = _cli(top / "2024", "migrate")
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
                self.assertIn(f"{bad}/tobase.map is not UTF-8 text (byte "
                              f"0xe9 at offset 47)", r.stderr)
                self.assertNotIn("is the one kept", r.stdout)
                self.assertNotIn("differ:", r.stdout)


class TestNewYearText(unittest.TestCase):
    def test_tj0190_new_year_next_steps_wrap_at_120(self):
        """TJ-0190: piped, NEXT STEPS line 1 was 141 columns."""
        with tempfile.TemporaryDirectory() as td:
            top = _years(td, (2024,))
            r = _ok(self, _cli(top, "new-year", "2025", width=None))
            self.assertIn("NEXT STEPS", r.stdout)
            long = [ln for ln in _long_lines(r.stdout)
                    if "tjs -C" not in ln]      # a command to copy
            self.assertEqual(long, [], r.stdout)
            self.assertIn("1. Save the 2025 exports in", r.stdout)

    def test_tj0191_reference_copy_lists_each_key_once(self):
        """TJ-0191: new-year's commented-out [estimate] / [instalments]
        held last year's line above the template's example for every key
        (`# other_income = 90000`, `# other_income = 0`): uncommenting it
        gave duplicate keys. Now one line per key, aligned, in the
        layout `tjs format` keeps."""
        from taxjson.lib import config_template as CT
        from taxjson.lib.tomlcompat import tomllib
        with tempfile.TemporaryDirectory() as td:
            top = _years(td, (2024,))
            cfg = top / "2024" / "taxjson.toml"
            cfg.write_text(cfg.read_text()
                           + "\n[estimate]\nother_income = 900\n"
                           "\n[instalments]\nbasis = \"prior_year\"\n"
                           "paid = [\n"
                           "  { date = 2024-03-15, amount = 500 },\n]\n"
                           "prior_year_net_tax = 400\n")
            _ok(self, _cli(top / "2024", "format", "--write",
                           "--no-backup"))
            _ok(self, _cli(top, "new-year", "2025"))
            text = (top / "2025" / "taxjson.toml").read_text()
            for key, want in (("other_income", "900"),
                              ("basis", '"prior_year"'),
                              ("paid", "["),
                              ("prior_year_net_tax", "400")):
                lines = [ln for ln in text.splitlines()
                         if ln.startswith(f"# {key} ")]
                self.assertEqual(len(lines), 1, (key, lines))
                self.assertTrue(lines[0].split("=", 1)[1].strip()
                                .startswith(want), lines)
            self.assertNotIn("other_income     = 0", text)
            # Aligned with the table's other keys.
            self.assertRegex(text, r"(?m)^# other_income     = 900$")
            self.assertRegex(text, r"(?m)^# carrying_charges = 0$")
            self.assertIn("already formatted", _ok(self, _cli(
                top / "2025", "format", "--check")).stdout)
            # The tables uncommented as the note says: no duplicate key.
            out, on = [], False
            for ln in text.splitlines():
                if ln in ("# [estimate]", "# [instalments]"):
                    on = True
                elif ln.startswith("# [") or not ln:
                    on = False
                out.append(ln[2:] if on and ln.startswith("# ") else ln)
            doc = tomllib.loads("\n".join(out))
            self.assertEqual(doc["estimate"]["other_income"], 900)
            self.assertEqual(doc["instalments"]["prior_year_net_tax"], 400)
            # A file an older release wrote (last year's line above the
            # example, both kept) stays formatted as it is.
            old = text.replace("# other_income     = 900\n",
                               "# other_income       = 900\n"
                               "# other_income     = 0\n")
            self.assertFalse(CT.format_config(old).changed)


class TestFoldDocs(unittest.TestCase):
    def test_tj0088_fold_steps_keep_a_frozen_year_reading_its_copy(self):
        """TJ-0088: getting-started's steps for folding year projects
        into one said to copy an older project's files but not its
        inputs/ and to add inputs_dir — for a year closed with its
        inputs frozen that leaves it naming the shared folder (the
        warning to delete the line) and no inputs of its own."""
        doc = (REPO_ROOT / "docs" / "getting-started.md").read_text()
        i = doc.index("To fold several year projects")
        para = " ".join(doc[i:doc.index("\n\n", i)].split())
        self.assertIn("copy its `inputs/<account>/` folders and its "
                      "`tobase.map` (if it has one) too, and leave "
                      "`inputs_dir` unset", para)
        self.assertIn('has no `"frozen"` entry', para)


if __name__ == "__main__":
    unittest.main()
