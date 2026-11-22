"""Backlog group `newyear`: `tjs new-year`, `tjs migrate` and a
filed year's frozen settings. One test per finding, each named for its
backlog id (TJ-NNNN). Synthetic data only (made-up tickers, .tt lines).

Formerly: test_fix_backlog_newyear
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from taxjson.lib.dispatch import python_module_argv
import _paths

REPO_ROOT = _paths.REPO

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


class TestNewYearGap(unittest.TestCase):
    """Formerly: test_fix_backlog_newyear.TestNewYearGap"""
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


class TestNewYearText(unittest.TestCase):
    """Formerly: test_fix_backlog_newyear.TestNewYearText"""
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
        import tomllib
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
    """Formerly: test_fix_backlog_newyear.TestFoldDocs"""
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
