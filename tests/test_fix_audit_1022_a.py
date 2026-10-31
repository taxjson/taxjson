"""Workflow audit 2026-10-22, items 1, 6, 11 and 16: a filed year closed
with its inputs frozen (close-year copied the shared exports and
tobase.map into its own folder).

- 1: `years` / `years --diff` / `align` leave a frozen year's
  inputs_dir and tobase_map out (align re-pointed the filed year at the
  shared exports); a filed year that names them again is told to delete
  the lines, never sent to `migrate`, and migrate keeps its copy.
- 6: `years --details` calls the frozen tobase.map the year's own, with
  no `migrate` hint (as the default view does).
- 11: a closed US year does not report the set of locked years as a
  frozen input that changed.
- 16: a file saved into the shared exports names the frozen filed years
  it does not reach; the docs say what the freeze does.

Synthetic data only.
"""
import io
import contextlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_fix_multi_year import multi, run_ok, single, tjs  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
_SHARED = 'inputs_dir = "../inputs"\ntobase_map = "../tobase.map"\n'


def _closed_2024():
    """A Canadian project of two year folders sharing inputs/ and
    tobase.map, 2024 closed (frozen), 2025 open."""
    top = multi("canada", years=(2024, 2025))
    (top / "tobase.map").write_text("# synthetic shared tobase.map\n")
    for y in (2024, 2025):
        p = top / str(y) / "taxjson.toml"
        p.write_text(p.read_text().replace(
            'inputs_dir = "../inputs"\n', _SHARED, 1))
    y24 = top / "2024"
    r = tjs("-C", str(y24), "run", "--no-input")
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    r = tjs("-C", str(y24), "close-year", "--yes")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (y24 / "tobase.map").is_file()
    assert "# inputs_dir" in (y24 / "taxjson.toml").read_text()
    return top


def _settings(folder: Path) -> dict:
    from taxjson.lib.tomlcompat import tomllib
    return tomllib.loads((folder / "taxjson.toml").read_text()).get(
        "settings") or {}


class TestFrozenYearSettingsApart(unittest.TestCase):
    """Item 1 (and 6)."""

    @classmethod
    def setUpClass(cls):
        cls.top = _closed_2024()

    def test_years_and_diff_leave_the_frozen_keys_out(self):
        doc = json.loads(tjs("years", "--json", cwd=self.top).stdout)
        y = {r["year"]: r for r in doc["years"]}
        self.assertEqual(y[2024]["differs_from_newest"]["keys"], 0, y[2024])
        r = tjs("years", "--diff", "2024", "2025", cwd=self.top)
        self.assertNotIn("inputs_dir", r.stdout + r.stderr)
        self.assertNotIn("tobase_map", r.stdout + r.stderr)

    def test_years_details_says_frozen_copy(self):
        r = tjs("years", "--details", cwd=self.top)
        out = r.stdout + r.stderr
        self.assertIn("frozen copy of its own in 2024", out)
        self.assertNotIn("migrate", out)

    def test_align_never_rewrites_the_frozen_keys(self):
        import shutil
        top = self.top.parent / "align"
        shutil.copytree(self.top, top)
        r = tjs("-C", str(top / "2024"), "align", "--from", "2025",
                "--write", "--all")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        st = _settings(top / "2024")
        self.assertNotIn("inputs_dir", st)
        self.assertNotIn("tobase_map", st)
        # Nor the other way: the open year keeps reading the shared ones.
        r = tjs("-C", str(top / "2025"), "align", "--from", "2024",
                "--write", "--all")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        st = _settings(top / "2025")
        self.assertEqual(st.get("inputs_dir"), "../inputs")
        self.assertEqual(st.get("tobase_map"), "../tobase.map")


class TestFrozenYearPointedBack(unittest.TestCase):
    """Item 1: a filed year whose taxjson.toml names the shared folders
    again (an older align wrote them) is told to delete the lines."""

    def setUp(self):
        self.top = _closed_2024()
        self.y24 = self.top / "2024"
        p = self.y24 / "taxjson.toml"
        p.write_text(p.read_text().replace("[settings]\n",
                                           "[settings]\n" + _SHARED, 1))

    def test_run_names_the_lines_not_migrate(self):
        r = tjs("-C", str(self.y24), "run", "--no-input")
        out = r.stdout + r.stderr
        self.assertNotEqual(r.returncode, 0, out)
        self.assertIn("two tobase.map files", out)
        self.assertIn("close-year froze", out)
        self.assertNotIn("tjs migrate", out)

    def test_migrate_keeps_the_frozen_copy(self):
        tjs("migrate", cwd=self.top)
        self.assertTrue((self.y24 / "tobase.map").is_file())
        self.assertFalse((self.y24 / "tobase.map.bak").exists())

    def test_drift_check_names_the_settings(self):
        # As an older migrate left it: the frozen tobase.map removed.
        (self.y24 / "tobase.map").rename(self.y24 / "tobase.map.bak")
        r = tjs("-C", str(self.y24), "check-filed")
        out = r.stdout + r.stderr
        self.assertIn("was closed with its inputs frozen, but taxjson.toml "
                      "names the shared inputs_dir (../inputs) and "
                      "tobase_map (../tobase.map) again", out)
        self.assertNotIn("restore them from your records", out)


class TestUsLockedYearsNotAFrozenInput(unittest.TestCase):
    """Item 11."""

    def test_closed_us_year_reports_no_frozen_change(self):
        root = single("usa", 2024)
        run_ok(self, root)
        r = tjs("-C", str(root), "close-year", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lock = json.loads((root / "filed" / "2024.json").read_text())
        from taxjson.lib.checklist import LOCKED_YEARS_KEY
        self.assertNotIn(LOCKED_YEARS_KEY, lock["frozen"]["inputs"])
        r = run_ok(self, root)
        self.assertNotIn("frozen input(s) changed", r.stdout + r.stderr)
        r = tjs("-C", str(root), "check-filed")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("frozen input(s) changed", r.stdout + r.stderr)
        r = tjs("-C", str(root), "compare", "filed")
        self.assertNotIn("locked years", r.stdout + r.stderr)

    def test_a_lock_written_before_the_fix_is_read_the_same(self):
        from taxjson.lib.checklist import (LOCKED_YEARS_KEY,
                                           fingerprint_changes)
        frozen = {"inputs/margin/trades.tt": "a"}
        now = {"inputs/margin/trades.tt": "a",
               LOCKED_YEARS_KEY: "locked-years:2024"}
        self.assertEqual(fingerprint_changes(frozen, now), [])
        now["inputs/margin/trades.tt"] = "b"
        self.assertEqual(fingerprint_changes(frozen, now),
                         ["inputs/margin/trades.tt"])


class TestSharedWriteNamesFrozenYears(unittest.TestCase):
    """Item 16: the note after a write into the shared exports."""

    def test_note_names_the_filed_year(self):
        from taxjson.bin import taxjson_run as R
        top = _closed_2024()
        f = top / "inputs" / "margin" / "opening_2025-01-01.tt"
        err = io.StringIO()
        with contextlib.redirect_stderr(err), \
                contextlib.redirect_stdout(err):
            R._SHOWN_THIS_RUN.clear()
            R._shared_inputs_note(top / "2025", f, "tjs opening")
        self.assertIn("applies to every year", err.getvalue())
        self.assertIn("Not 2024 (filed, inputs frozen)", err.getvalue())

    def test_docs_no_longer_promise_drift_from_shared_inputs(self):
        gs = (REPO / "docs" / "getting-started.md").read_text()
        self.assertNotIn("A new export in `inputs/` can\n  change a filed "
                         "year's figures", gs)
        self.assertIn("never reaches it", gs)
        fl = (REPO / "docs" / "filing.md").read_text()
        self.assertNotIn("amended, not silently absorbed", fl)
        self.assertIn("handoff", fl)


if __name__ == "__main__":
    unittest.main()
