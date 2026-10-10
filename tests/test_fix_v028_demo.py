"""v0.28.0 demo-project glitches.

* `taxjson wash-sales --explain` traced the demo's one superficial loss
  and still said "no matching gains found" on stderr: the crypto book
  (traced on its own) had none. The note is now said once, only when no
  book had a denial.
* `taxjson sum --other-income ...`: the estimate's "[X realized ...]"
  was the unrounded engine total, a cent off the RETURN row's gain (the
  per-row cents the user files). It is now the RETURN figure.

Synthetic data only (`taxjson init --demo`, tests/fixtures/style), the
hermetic HOME's made-up rates; offline.
"""
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _style import env, project
from tax_rules import rule


def _money(s: str) -> float:
    return float(s.replace(",", ""))


def _return_gain(stdout: str) -> float:
    """The GAIN column of the Schedule 3 RETURN row."""
    row = next(ln for ln in stdout.splitlines()
               if ln.startswith("RETURN "))
    # PROCEEDS COST(ACB) OUTLAYS GAIN DENIED
    return _money(row.split()[4])


def _estimate_realized(stdout: str) -> float:
    m = re.search(r"\[(-?[\d,]+\.\d\d) realized", stdout)
    assert m, stdout[-3000:]
    return _money(m.group(1))


class _Demo:
    tmp = None
    root = None

    @classmethod
    def get(cls) -> Path:
        if cls.root is None:
            cls.tmp = tempfile.mkdtemp(prefix="taxjson_demo_")
            r = cls.cli(None, "init", "--demo", str(Path(cls.tmp) / "d"))
            assert r.returncode == 0, r.stderr
            cls.root = Path(cls.tmp) / "d" / "2024"
            r = cls.cli(cls.root, "run", "--no-input")
            assert r.returncode == 0, r.stderr[-2000:]
        return cls.root

    @staticmethod
    def cli(root, *args):
        cmd = [sys.executable, "-m", "taxjson.bin.taxjson_run"]
        if root is not None:
            cmd += ["-C", str(root)]
        return subprocess.run(cmd + list(args), capture_output=True,
                              text=True, env=env(), timeout=900,
                              stdin=subprocess.DEVNULL)

    @classmethod
    def cleanup(cls):
        if cls.tmp:
            shutil.rmtree(cls.tmp, ignore_errors=True)


def tearDownModule():
    _Demo.cleanup()


class TestWashExplainNoMatchNote(unittest.TestCase):
    def test_denial_traced_without_no_match_note(self):
        root = _Demo.get()
        r = _Demo.cli(root, "wash-sales", "--explain")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Superficial loss", r.stdout)
        self.assertIn("triggered by", r.stdout)
        self.assertNotIn("no matching gains", r.stdout + r.stderr)

    def test_note_once_when_no_book_matches(self):
        # The crypto book alone has no denial: the note, said once.
        root = _Demo.get()
        r = _Demo.cli(root, "wash-sales", "--explain", "crypto")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("triggered by", r.stdout)
        self.assertEqual((r.stdout + r.stderr).count(
            "no matching gains found"), 1, r.stderr)


class TestEstimateRealizedIsReturnGain(unittest.TestCase):
    def _check(self, stdout: str):
        self.assertEqual(_estimate_realized(stdout), _return_gain(stdout),
                         stdout[-3000:])

    @rule("CA-RPT-03")
    def test_style_canada(self):
        r = project("canada").run("sum", "--other-income", "50000",
                                  "--province", "ON")
        self.assertEqual(r.returncode, 0, r.stderr)
        self._check(r.stdout)

    @rule("CA-RPT-03")
    def test_demo(self):
        r = _Demo.cli(_Demo.get(), "sum", "--other-income", "50000",
                      "--province", "ON")
        self.assertEqual(r.returncode, 0, r.stderr)
        self._check(r.stdout)


if __name__ == "__main__":
    unittest.main()
