"""Essentials first (docs/output-style.md) for the set-up commands: init,
new-year, migrate, redact, elect, format and crypto-sends keep their
default view within the budget (non-table lines, stdout and stderr
together, at TAXJSON_WIDTH=100), and `--details` prints what the default
leaves out. Synthetic style projects only (tests/_style.py)."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _style
from _style import assert_concise, assert_console, project

W = {"TAXJSON_WIDTH": "100"}


def _flat(*texts) -> str:
    return " ".join(" ".join(texts).split())


def _tjs(*args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "taxjson.bin.taxjson_run", *args],
        cwd=cwd, capture_output=True, text=True, env=_style.env(**W),
        stdin=subprocess.DEVNULL, timeout=600)


class TestInitAndNewYear(unittest.TestCase):

    def test_init_both_countries(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country), \
                    tempfile.TemporaryDirectory() as td:
                r = _tjs("init", "--country", country, "--year", "2024",
                         str(Path(td) / "p"), cwd=td)
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_concise(self, r.stdout, r.stderr)
                self.assertIn("NEXT STEPS", r.stdout)
                for step in ("  3. taxjson -C ", "  4. taxjson -C "):
                    self.assertIn(step, r.stdout)
                self.assertNotIn("wrote ", r.stdout)
                d = _tjs("init", "--country", country, "--year", "2024",
                         "--force", "--details", str(Path(td) / "p"),
                         cwd=td)
                self.assertEqual(d.returncode, 0, d.stderr)
                self.assertIn("wrote 2024/taxjson.toml", d.stdout)
                self.assertIn("Next year:", d.stdout)
                if country == "usa":
                    self.assertIn("EXPERIMENTAL: treat the output as a "
                                  "draft", r.stderr)
                    self.assertIn("contributing a redacted export",
                                  _flat(d.stderr))

    def test_init_force_keeps_the_backup_line(self):
        with tempfile.TemporaryDirectory() as td:
            p = str(Path(td) / "p")
            _tjs("init", "--single", "--country", "ca", p, cwd=td)
            r = _tjs("init", "--single", "--country", "us", "--force", p,
                     cwd=td)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("wrote taxjson.toml.bak (your previous config)",
                          r.stdout)
            self.assertIn("re-add (see taxjson.toml.bak)", r.stdout)
            # 6, plus the two must-not-miss lines of a --force over
            # another country's project: the backup, the orphan folders.
            assert_concise(self, r.stdout, r.stderr, budget=8)
            for ln in r.stdout.splitlines():
                self.assertLessEqual(len(ln), 100, ln)

    def test_new_year(self):
        with tempfile.TemporaryDirectory() as td:
            top = Path(td) / "t"
            _tjs("init", "--country", "canada", "--year", "2024", str(top),
                 cwd=td)
            r = _tjs("-C", str(top), "new-year", "2025", cwd=td)
            self.assertEqual(r.returncode, 0, r.stderr)
            assert_concise(self, r.stdout, r.stderr)
            self.assertIn("2025/holdings/", r.stdout)
            self.assertIn("taxjson align --from 2024", r.stdout)
            self.assertIn("2024 is not locked yet", r.stderr)
            shutil.rmtree(top / "2025")
            d = _tjs("-C", str(top), "new-year", "2025", "--details",
                     cwd=td)
            self.assertEqual(d.returncode, 0, d.stderr)
            self.assertIn("prior_year_record ../2024/filed/2024.json",
                          _flat(d.stdout))
            self.assertIn("keep those: the books are built from all of "
                          "them", _flat(d.stdout))


class TestElect(unittest.TestCase):

    def test_pending(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                p = project(country, pending=True)
                r = p.run("elect", "--pending", **W)
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_concise(self, r.stdout, r.stderr)
                out = r.stdout.splitlines()
                # The checklist reads the last line (lib/checklist).
                self.assertTrue(out[-1].startswith("1 election pending: "),
                                out[-1])
                self.assertIn("WHAT IT BOOKS", r.stdout)
                self.assertIn("ignore", r.stdout)
                self.assertIn("=OPTION [--hint KEY=VALUE]", r.stdout)
                d = p.run("elect", "--pending", "--details", **W)
                self.assertEqual(d.returncode, 0, d.stderr)
                self.assertIn("ONLY for broker noise", _flat(d.stdout))
                self.assertIn("set:  taxjson elect margin --set ",
                              d.stdout)
                self.assertEqual(d.stdout.splitlines()[-1], out[-1])

    def test_listing(self):
        for args in (("elect",), ("elect", "margin")):
            with self.subTest(args=args):
                r = project("canada").run(*args, **W)
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_concise(self, r.stdout, r.stderr)


class TestFormatMigrateRedact(unittest.TestCase):

    def test_format_note_is_one_line(self):
        r = project("canada").run("format", **W)
        self.assertEqual(r.returncode, 0, r.stderr)
        # The diff is the data; the closing note is one line.
        assert_concise(self, r.stderr, budget=1)
        self.assertIn("+++ taxjson.toml (formatted)", r.stdout)
        d = project("canada").run("format", "--details", **W)
        self.assertIn("loads the same either way", _flat(d.stderr))

    def test_migrate_nothing(self):
        r = project("canada").run("migrate", "--dry-run", **W)
        self.assertEqual(r.returncode, 0, r.stderr)
        assert_concise(self, r.stdout, r.stderr, budget=1)
        d = project("canada").run("migrate", "--dry-run", "--details", **W)
        self.assertIn("None of ", d.stdout)

    def test_redact_project(self):
        src = project("canada")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "p"
            shutil.copytree(src.root, root)
            p = _style.Project(root, "canada")
            r = p.run("redact", "--no-denylist", **W)
            self.assertEqual(r.returncode, 0, r.stderr)
            assert_concise(self, r.stdout, r.stderr)
            assert_console(self, r.stdout, width=100)
            self.assertRegex(r.stdout, r"Info: \d+ of \d+ files redacted")
            d = p.run("redact", "--no-denylist", "--force", "--details",
                      **W)
            self.assertEqual(d.returncode, 0, d.stderr)
            self.assertIn("Info: margin/ib_demo.csv: 1 account id",
                          d.stdout)
            self.assertIn("nothing to redact", d.stdout)

    def test_redact_check_file(self):
        r = _tjs("redact", "--check",
                 str(_style.REPO_ROOT / "examples" / "questrade_demo.csv"),
                 cwd=_style.REPO_ROOT)
        self.assertEqual(r.returncode, 1, r.stderr)
        assert_concise(self, r.stdout, r.stderr)


class TestCryptoSends(unittest.TestCase):

    def test_both_countries(self):
        for country in ("canada", "usa"):
            with self.subTest(country=country):
                p = project(country)
                r = p.run("crypto-sends", **W)
                self.assertEqual(r.returncode, 0, r.stderr)
                assert_concise(self, r.stdout, r.stderr)
                self.assertIn("self: a move to your own wallet",
                              r.stdout.splitlines()[1])
                self.assertNotIn("matched to an arrival", r.stdout)
                d = p.run("crypto-sends", "--details", **W)
                self.assertIn("A move to your own wallet is not a sale "
                              "(self)", _flat(d.stdout))
                self.assertIn("matched to an arrival", d.stdout)


if __name__ == "__main__":
    unittest.main()
