"""Pre-release review 022 (synthetic QZ-style data only).

M2  a name-only truncation match needs width evidence: a complete last
    word that starts a longer word is not a cut.
M3  a broker's event wording is cut only from Questrade descriptions, and
    a designator in the cut part is carried / refuses one-sided tolerance.
M4  a long whitespace run in a name is collapsed before any regex.
L3  a listing joined / suggested must be one ticker.map token.
M5  a short carried through a rename keeps its pair in the year scope.
L1  a quoted broker description in a .diag is never a suggestion.
L2  `ticker-map --write` keeps a symlinked ticker.map a link (mode kept).
L4  --outside-year counts only its own entries, refuses a non-object
    entry before writing, and keeps a symlinked file a link.
L5  the wash radar honours --transfers-as-acquisitions.
M1  the default transfer policy lists the netted moves (cross-account and
    zero-net clusters) inside a loss's window in its one warning; the
    main-book SPLIT guard holds in cross-account netting.

Formerly: test_fix_prerelease_022
"""
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path


class TestL4OutsideYearWrites(unittest.TestCase):
    """The mhscope project: QPAS / QOPN (margin) and QRGL (rrsp) went
    short in 2024 and never trade again.

    Formerly: test_fix_prerelease_022.TestL4OutsideYearWrites"""

    @classmethod
    def setUpClass(cls):
        from _support.ib_missing_history import _make, _tj
        cls.tmp, cls.base = _make("canada")
        r = _tj(cls.base, "run", "--no-input")
        assert r.returncode == 0, r.stderr + r.stdout

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, True)

    def _copy(self):
        d = tempfile.mkdtemp(prefix="taxjson_l4_")
        self.addCleanup(shutil.rmtree, d, True)
        root = Path(d) / "canada"
        shutil.copytree(self.base, root, symlinks=True)
        return root

    def _outside(self, root):
        from _support.ib_missing_history import _tj
        return _tj(root, "find-missing-history",
                   "--write-missing-history", "--outside-year")

    def _tt(self, root, acct="margin"):
        return root / "inputs" / acct / "missing_history.tt"

    def test_your_lines_are_kept(self):
        root = self._copy()
        mine = "OPENING 2020-01-02 QPAS.TO 999 cost=unknown\n"
        self._tt(root).write_text(mine)
        r = self._outside(root)
        self.assertEqual(r.returncode, 0, r.stderr)
        flat = " ".join(r.stderr.split())
        self.assertIn("opens 999, the books size", flat)
        text = self._tt(root).read_text()
        self.assertTrue(text.startswith(mine))
        self.assertEqual([ln.split()[2] for ln in text.splitlines()
                          if ln.startswith("OPENING ")],
                         ["QPAS.TO", "QOPN.TO"])

    def test_symlinked_file_stays_a_link(self):
        root = self._copy()
        (root / "data").mkdir()
        real = root / "data" / "mh.tt"
        real.write_text("OPENING 2020-01-02 QHAND.TO 5 cost=unknown\n")
        os.chmod(real, 0o644)
        self._tt(root).symlink_to("../../data/mh.tt")
        r = self._outside(root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self._tt(root).is_symlink())
        self.assertEqual(sum(1 for ln in real.read_text().splitlines()
                             if ln.startswith("OPENING ")), 3)
        self.assertEqual(stat.S_IMODE(real.stat().st_mode), 0o644)

    def test_symlink_leaving_the_project_is_refused(self):
        root = self._copy()
        outside = root.parent / "elsewhere.tt"
        outside.write_text("")
        self._tt(root).symlink_to(outside)
        r = self._outside(root)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("outside the project", " ".join(r.stderr.split()))
        self.assertEqual(outside.read_text(), "")


if __name__ == "__main__":
    unittest.main()
