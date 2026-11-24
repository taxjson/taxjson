"""Backlog TJ-5088 (the same defect as TJ-4974's open item 21): a later
year beside this one whose taxjson.toml cannot be read is still a
dependency of `tjs unclose-year`.

The later years are found by their folder (a year folder, `2025/`) or by
what their taxjson.toml says (a later `year`, or a `prior_year_record`
naming this year's lock). A link `2025 -> elsewhere` is no year folder, so
with an unparsable taxjson.toml nothing said which year it was, and
reopening 2024 removed its lock with the later filed lock left in place.
A folder beside this one whose filed/ holds a later year's lock, a link
or not, is now a refusal when its taxjson.toml cannot be read, is a link
to nothing, or sets no year (a string is no year), and so is one named as
a later year (`2025`, a link so named too) whose taxjson.toml is missing:
named, with why and the remedy. So is one whose filed/ cannot be listed,
its taxjson.toml unreadable, and a readable later year's whose filed/
cannot be listed: it may hold a later lock. A later lock in a
readable later year is the refusal it was, named before a broken copy of
that year. A folder of another name with no taxjson.toml is no project.

Each case on a minimal closed year (a taxjson.toml and filed/2024.json:
the refusal comes before the lock is read). Synthetic folders only."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Optional

import _hermetic  # (a synthetic HOME, offline: tests/_hermetic)
from taxjson.lib.dispatch import python_module_argv
import _paths

YEAR, NEXT = "2024", "2025"
_BAD_TOML = '[settings]\nyear = 2025\ncountry = "canada\n'      # an unclosed string


def _cli(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """`tjs ARGS` as a child of this tree's code, offline, width 0."""
    env = _hermetic.environ(TAXJSON_WIDTH="0", NO_COLOR="1", TAXJSON_OFFLINE="1")
    env["PYTHONPATH"] = str(_paths.SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(python_module_argv("taxjson.bin.taxjson_run", list(args)),
                          cwd=cwd, capture_output=True, text=True, env=env,
                          stdin=subprocess.DEVNULL, timeout=600)


class UncloseYearUnreadableLaterYear(unittest.TestCase):

    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.top = Path(t.name) / "p"
        y = self.top / YEAR
        (y / "filed").mkdir(parents=True)
        (y / "taxjson.toml").write_text(
            f'[settings]\nyear = {YEAR}\ncountry = "canada"\nbase_currency = "CAD"\n',
            encoding="utf-8")
        self.lock = y / "filed" / f"{YEAR}.json"
        self.lock.write_text(f'{{"year": {YEAR}}}\n', encoding="utf-8")

    def _later(self, folder: str, toml: Optional[str], *, link: bool = False,
               lock: bool = True) -> Path:
        d = self.top / folder
        (d / "filed").mkdir(parents=True)
        if toml is not None:
            (d / "taxjson.toml").write_text(toml, encoding="utf-8")
        if lock:
            (d / "filed" / f"{NEXT}.json").write_text(f'{{"year": {NEXT}}}\n',
                                                      encoding="utf-8")
        if link:
            os.symlink(folder, self.top / NEXT)
        return d

    def _refused(self, r: subprocess.CompletedProcess, shown: str, why: str) -> None:
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"tjs unclose-year: error: {shown} holds the lock of {NEXT} "
                      f"({shown}/filed/{NEXT}.json), a later year than {YEAR}, but "
                      f"{shown}/taxjson.toml {why}", r.stderr)
        self.assertIn(f"Repair {shown}/taxjson.toml, then reopen the newest year first "
                      f"(`tjs -C {shown} unclose-year`), then {YEAR}; if {shown} is not "
                      f"a later year of this project, move it out of this folder's "
                      f"parent. Nothing was changed.", r.stderr)
        self.assertNotIn("REOPENED", r.stdout)
        self.assertTrue(self.lock.is_file())
        self.assertEqual(sorted(p.name for p in self.lock.parent.iterdir()),
                         [f"{YEAR}.json"])

    def test_a_linked_later_year_whose_taxjson_toml_cannot_be_read_is_a_refusal(self):
        # The entry's repro: `2025 -> elsewhere`, a later lock, an unparsable
        # taxjson.toml. It reopened 2024 (exit 0).
        self._later("elsewhere", _BAD_TOML, link=True)
        for argv in (("unclose-year", "--dry-run"), ("unclose-year",)):
            with self.subTest(argv=argv):
                self._refused(_cli(self.top / YEAR, *argv), f"../{NEXT}",
                              "is not valid TOML: ")

    def test_a_folder_of_another_name_is_a_refusal_too(self):
        self._later("next-year", _BAD_TOML)
        self._refused(_cli(self.top / YEAR, "unclose-year"), "../next-year",
                      "is not valid TOML: ")

    def test_a_taxjson_toml_that_sets_no_year_is_a_refusal(self):
        self._later("elsewhere", '[settings]\ncountry = "canada"\n', link=True)
        self._refused(_cli(self.top / YEAR, "unclose-year"), f"../{NEXT}",
                      "sets no [settings] year")

    def test_a_taxjson_toml_that_cannot_be_opened_is_a_refusal(self):
        d = self._later("elsewhere", f'[settings]\nyear = {NEXT}\n', link=True)
        (d / "taxjson.toml").chmod(0)
        self.addCleanup((d / "taxjson.toml").chmod, 0o600)
        self._refused(_cli(self.top / YEAR, "unclose-year"), f"../{NEXT}",
                      "cannot be read: ")

    def test_a_year_folder_whose_taxjson_toml_cannot_be_read_says_why(self):
        # A year folder by its name was a refusal already ("closed too"),
        # whose remedy ran into the broken file: it says why now.
        self._later(NEXT, _BAD_TOML)
        self._refused(_cli(self.top / YEAR, "unclose-year"), f"../{NEXT}",
                      "is not valid TOML: ")

    def test_a_later_year_whose_taxjson_toml_is_a_link_to_nothing_is_a_refusal(self):
        # `2025 -> elsewhere`, its taxjson.toml a link to nothing: reopened
        # (exit 0) before.
        d = self._later("elsewhere", None, link=True)
        os.symlink(str(self.top / "gone" / "taxjson.toml"), d / "taxjson.toml")
        self._refused(_cli(self.top / YEAR, "unclose-year"), f"../{NEXT}",
                      f"is a link to nothing ({self.top}/gone/taxjson.toml)")

    def test_a_later_year_with_no_taxjson_toml_is_a_refusal(self):
        # Named as the year, with a later lock and no taxjson.toml:
        # reopened before.
        self._later(NEXT, None)
        self._refused(_cli(self.top / YEAR, "unclose-year"), f"../{NEXT}", "is missing")

    def test_a_link_named_as_a_later_year_with_no_taxjson_toml_is_a_refusal(self):
        self._later("elsewhere", None, link=True)
        self._refused(_cli(self.top / YEAR, "unclose-year"), f"../{NEXT}", "is missing")

    def test_a_folder_of_another_name_with_no_taxjson_toml_is_no_project(self):
        # Control: `stuff/filed/2025.json` and no taxjson.toml: reopened.
        self._later("stuff", None)
        r = _cli(self.top / YEAR, "unclose-year", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_filed_folder_that_cannot_be_listed_is_a_refusal(self):
        # Its taxjson.toml unparsable and its filed/ unreadable: it may
        # hold a later lock. Reopened before.
        d = self._later("elsewhere", _BAD_TOML)
        (d / "filed").chmod(0)
        self.addCleanup((d / "filed").chmod, 0o700)
        r = _cli(self.top / YEAR, "unclose-year")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"tjs unclose-year: error: ../elsewhere may hold the lock of a "
                      f"later year than {YEAR}: ../elsewhere/filed cannot be listed "
                      f"(Permission denied) and ../elsewhere/taxjson.toml is not "
                      f"valid TOML: ", r.stderr)
        self.assertIn("Repair ../elsewhere/taxjson.toml and ../elsewhere/filed, then "
                      "reopen the newest year first (`tjs -C ../elsewhere "
                      "unclose-year`)", r.stderr)
        self.assertTrue(self.lock.is_file())

    def test_a_readable_later_year_whose_filed_folder_cannot_be_listed_is_a_refusal(self):
        # The review's: 2025 readable, its filed/ mode 000. Reopened before
        # (no lock seen); it may hold 2025's lock, so it is named as that.
        d = self._later(NEXT, f'[settings]\nyear = {NEXT}\ncountry = "canada"\n')
        (d / "filed").chmod(0)
        self.addCleanup((d / "filed").chmod, 0o700)
        r = _cli(self.top / YEAR, "unclose-year")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn(f"tjs unclose-year: error: ../{NEXT} may hold the lock of a "
                      f"later year than {YEAR}: ../{NEXT}/filed cannot be listed "
                      f"(Permission denied)\n", r.stderr)
        self.assertIn(f"Repair ../{NEXT}/filed, then reopen the newest year first "
                      f"(`tjs -C ../{NEXT} unclose-year`), then {YEAR}", r.stderr)
        self.assertNotIn("REOPENED", r.stdout)
        self.assertTrue(self.lock.is_file())

    def test_a_year_set_as_a_string_is_said_so(self):
        self._later("elsewhere", f'[settings]\nyear = "{NEXT}"\n')
        self._refused(_cli(self.top / YEAR, "unclose-year"), "../elsewhere",
                      f'sets [settings] year = "{NEXT}", a string, not a year')

    def test_the_readable_year_folder_is_named_before_a_broken_copy(self):
        # ../2025 readable and closed, ../2025-backup broken with the same
        # lock: the remedy names ../2025, which can be reopened.
        self._later(NEXT, f'[settings]\nyear = {NEXT}\ncountry = "canada"\n')
        self._later(f"{NEXT}-backup", _BAD_TOML)
        r = _cli(self.top / YEAR, "unclose-year")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn(f"tjs unclose-year: error: {NEXT} is closed too "
                      f"(../{NEXT}/filed/{NEXT}.json), on what {YEAR} carried into it",
                      r.stderr)
        self.assertIn(f"(`tjs -C ../{NEXT} unclose-year`)", r.stderr)

    def test_a_readable_later_year_closed_is_the_refusal_it_was(self):
        # Control: readable, closed: "closed too", as before.
        self._later(NEXT, f'[settings]\nyear = {NEXT}\ncountry = "canada"\n')
        r = _cli(self.top / YEAR, "unclose-year")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn(f"tjs unclose-year: error: {NEXT} is closed too "
                      f"(../{NEXT}/filed/{NEXT}.json), on what {YEAR} carried into it",
                      r.stderr)
        self.assertTrue(self.lock.is_file())

    def test_a_folder_beside_with_no_later_lock_is_no_dependency(self):
        # Control: unreadable, but its filed/ holds no later lock (and a
        # lock of this year or before is none either): reopened as before.
        d = self._later("elsewhere", _BAD_TOML, link=True, lock=False)
        (d / "filed" / f"{YEAR}.json").write_text(f'{{"year": {YEAR}}}\n',
                                                  encoding="utf-8")
        r = _cli(self.top / YEAR, "unclose-year", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"DRY RUN — reopening {YEAR} would change", r.stdout)


if __name__ == "__main__":
    unittest.main()
