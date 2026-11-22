"""Backlog fix round 7 (wave B) of 2026-10-29, group `forms`: form
export, carryover and the cash view.

TJ-0895  a commission-only disposal of unknown-cost units: its manual
         row keeps net proceeds -2, never "proceeds 2".
TJ-0896  US carryover: a loss still carried after the last year with a
         sale takes each later year's 3,000 up to the project year.
TJ-0898  a closed year's T5 box 18 capital-gains dividends stay in the
         carry-back room the next year's carryover offers.
TJ-0901  the standalone Form 8949 takes its year from the gains files
         (or the trade date), not a December trade's January settlement.
TJ-0904  taxjson-carryover --filed-lock YEAR=PATH refuses a lock of
         another year than YEAR.
TJ-0905  the standalone form export reads a gains file named twice once.
TJ-0889  `tjs cash`: a copied line keeps its book (at=alpha), so the
         copy reads into the book the original did.
TJ-0693  `tjs cash`: a stock-lending receipt is a #CREDIT line and an
         other credit, never a fee paid.

Each test cites its backlog id (TJ-NNNN). Every fixture is SYNTHETIC:
made-up tickers (SYN*, ZZ*), account names (margin), amounts under
1,000 (or round test figures).

Formerly: test_fix_1029_r7b_forms
"""
import csv
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
from _support.filing_forms_project import _env, _tjs, _project, _run


def _module(mod, *args, cwd=None):
    """A standalone tool (`taxjson-form-export` ...) in a child process."""
    return subprocess.run(python_module_argv(mod) + [str(a) for a in args],
                          capture_output=True, text=True, env=_env(),
                          cwd=cwd, stdin=subprocess.DEVNULL, timeout=600)


class TestManualRowKeepsCommissionDebit(unittest.TestCase):
    """TJ-0895: 10 SYN held before the data at an unknown cost, sold
    for nothing with a 2 commission (net proceeds -2). The manual
    reporting row read abs(proceeds) and told the filer to report
    proceeds 2 received, in the text and the CSV of both countries.

    Formerly: test_fix_1029_r7b_forms.TestManualRowKeepsCommissionDebit"""

    def test_unit_long_sign_kept_short_cover_magnitude(self):
        from taxjson.bin.taxjson_form_export import manual_figures
        self.assertEqual(manual_figures({"proceeds": -2.0,
                                         "direction": "LONG"}),
                         (-2.0, None))
        self.assertEqual(manual_figures({"proceeds": 50.0}), (50.0, None))
        # A short cover keeps its cover cost as a magnitude (TJ-0497).
        self.assertEqual(manual_figures({"proceeds": -50.0,
                                         "direction": "SHORT"}),
                         (None, 50.0))

    def test_both_countries_text_and_csv(self):
        for country, cur, sfx in (("canada", "CAD", "TO"),
                                  ("usa", "USD", "US")):
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as d:
                root = _project(
                    Path(d) / "p", country=country,
                    tt=(f"OPENING 2024-12-02 SYN.{sfx} 10 cost=unknown\n"
                        f"BUYSELL 2025-03-03 10:00:00 SYN.{sfx} -10 {cur} "
                        f"0 -2 2\n"))
                _run(self, root)
                r = _tjs(root, "form-export", "--details")
                self.assertEqual(r.returncode, 0, r.stderr)
                out = r.stdout + r.stderr
                self.assertIn(f"proceeds -2.00 {cur}", out)
                self.assertNotIn(f"proceeds 2.00 {cur}", out)
                self.assertIn("(-2.00)", out)
                f = Path(d) / "form.csv"
                r = _tjs(root, "form-export", "--csv", str(f))
                self.assertEqual(r.returncode, 0, r.stderr)
                rows = [row for row in
                        csv.reader(f.read_text().splitlines())
                        if row and row[0] == "MANUAL"]
                self.assertEqual(len(rows), 1)
                self.assertIn("-2.0", rows[0])
                self.assertNotIn("2.0", rows[0])


class TestStandaloneFormFileOnce(unittest.TestCase):
    """TJ-0905: the same gains file given twice to taxjson-form-export
    (explicit paths and a shell glob that overlap) doubled the
    Schedule 3 totals at readiness complete.

    Formerly: test_fix_1029_r7b_forms.TestStandaloneFormFileOnce"""

    def test_repeated_path_read_once(self):
        with tempfile.TemporaryDirectory() as d:
            root = _project(
                Path(d) / "p", year=2024,
                tt=("BUYSELL 2024-01-03 10:00:00 SYN.TO 10 CAD 10 100 0\n"
                    "BUYSELL 2024-06-03 10:00:00 SYN.TO -10 CAD 20 200 0\n"),
                settings="option_grant_timing_since = 2024\n")
            _run(self, root)
            g = root / "work" / "margin_gains.json"
            args = ("--country", "canada", "--form", "schedule3",
                    "--year", "2024", "--json")
            one = _module("taxjson.bin.taxjson_form_export", g, *args)
            self.assertEqual(one.returncode, 0, one.stderr)
            # The same file twice, once by another spelling of its path.
            two = _module("taxjson.bin.taxjson_form_export", g,
                          root / "work" / ".." / "work" / g.name, *args)
            self.assertEqual(two.returncode, 0, two.stderr)
            a, b = json.loads(one.stdout), json.loads(two.stdout)
            self.assertEqual(a["totals"], b["totals"])
            self.assertEqual(a["totals"]["gain_all"], 100.0)
            self.assertIn("given more than once", two.stderr)

    def test_unit_unique_paths(self):
        from taxjson.bin.taxjson_form_export import _unique_paths
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.json", Path(d) / "b.json"
            once, again = _unique_paths([a, b, Path(d) / "." / "a.json"])
            self.assertEqual(once, [a, b])
            self.assertEqual(len(again), 1)


class TestFiledLockYearBinding(unittest.TestCase):
    """TJ-0904: a genuine 2024 lock (gain 100) passed to the standalone
    taxjson-carryover as `--filed-lock 2021=.../2024.json` was taken as
    2021's filed return at exit 0, and the 2024 carry-back room of the
    2025 loss was gone.

    Formerly: test_fix_1029_r7b_forms.TestFiledLockYearBinding"""

    def test_binding_checked(self):
        with tempfile.TemporaryDirectory() as d:
            p = _project(
                Path(d) / "y2024", year=2024,
                tt=("BUYSELL 2024-01-03 10:00:00 SYN.TO 10 CAD 10 100 0\n"
                    "BUYSELL 2024-06-03 10:00:00 SYN.TO -10 CAD 20 200 0\n"),
                settings="option_grant_timing_since = 2024\n")
            _run(self, p)
            r = _tjs(p, "close-year", "--yes", "--force")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            q = _project(
                Path(d) / "y2025", year=2025,
                tt=("BUYSELL 2025-01-03 10:00:00 LOSS.TO 10 CAD 20 200 0\n"
                    "BUYSELL 2025-06-03 10:00:00 LOSS.TO -10 CAD 10 100 0\n"),
                settings="option_grant_timing_since = 2025\n")
            _run(self, q)
            lock = p / "filed" / "2024.json"

            def carry(binding):
                return _module(
                    "taxjson.bin.taxjson_carryover",
                    q / "work" / "margin_base.json", "--country", "canada",
                    "--project-year", "2025", "--filed-lock",
                    f"{binding}={lock}", "--json")

            bad = carry(2021)
            self.assertEqual(bad.returncode, 2, bad.stdout + bad.stderr)
            self.assertIn("the lock is for 2024, not 2021", bad.stderr)
            self.assertEqual(bad.stdout, "")
            # A lock named anything: its own year field decides.
            other = Path(d) / "record.json"
            other.write_text(lock.read_text())
            bad = _module(
                "taxjson.bin.taxjson_carryover",
                q / "work" / "margin_base.json", "--country", "canada",
                "--project-year", "2025", "--filed-lock",
                f"2021={other}", "--json")
            self.assertEqual(bad.returncode, 2, bad.stdout + bad.stderr)
            self.assertIn("the lock records year 2024, not 2021",
                          bad.stderr)
            good = carry(2024)
            self.assertEqual(good.returncode, 0, good.stderr)
            rows = {r["year"]: r for r in json.loads(good.stdout)["rows"]}
            self.assertEqual([(c["year"], c["amount"]) for c in
                              rows[2025]["carryback_candidates"]],
                             [(2024, 100.0)])


if __name__ == "__main__":
    unittest.main()
