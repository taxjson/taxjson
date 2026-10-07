"""Pre-release review fixes (low): synthetic data only.

F5  — a command started without stdout or stderr (`>&-`, `2>&-`: Python
      sets the stream to None) printed a traceback / stopped with exit 1:
      lib/out.settling_streams wrapped the None stream.
F8b — the listing-suffix correction (lib/listing_suffix) moved rows away
      from a QUOTE / T1135 line written for the `.US` spelling: a
      ticker.map line naming the symbol in any keyword now blocks it.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from test_fix_a2_errb import _env, _make_project  # noqa: E402
from tax_rules import rule  # noqa: E402


def _closed(fd, root, home, *a):
    """`taxjson -C root *a` with file descriptor `fd` (1 or 2) closed,
    the other captured. -> (returncode, captured text)."""
    keep = 2 if fd == 1 else 1
    code = (f"import os, subprocess, sys; os.close({fd}); "
            f"sys.exit(subprocess.call(sys.argv[1:], stdin=subprocess.DEVNULL,"
            f" close_fds=False))")
    r = subprocess.run(
        [sys.executable, "-c", code, sys.executable, "-m",
         "taxjson.bin.taxjson_run", "-C", str(root), *a],
        cwd=str(root), env=_env(home),
        stdout=subprocess.PIPE if keep == 1 else subprocess.DEVNULL,
        stderr=subprocess.PIPE if keep == 2 else subprocess.DEVNULL,
        text=True)
    return r.returncode, (r.stdout if keep == 1 else r.stderr) or ""


class TestClosedStreams(unittest.TestCase):
    """F5: `tjs sum >&-` and `tjs run --no-input 2>&-` exit 0."""

    @classmethod
    def setUpClass(cls):
        cls._td = tempfile.TemporaryDirectory()
        td = Path(cls._td.name)
        cls.home = td / "home"
        cls.home.mkdir()
        cls.root = td / "p"
        _make_project(cls.root)
        # A retired ticker.map line: the run's Info about it is printed
        # to stderr by the run itself (not a stage).
        (cls.root / "ticker.map").write_text(
            "TRADINGVIEW ABC.TO TSX:ABC\n")

    @classmethod
    def tearDownClass(cls):
        cls._td.cleanup()

    def test_run_with_stderr_closed(self):
        rc, out = _closed(2, self.root, self.home, "run", "--no-input")
        self.assertEqual(rc, 0, out[-2000:])
        self.assertIn("Done.", out)
        self.assertTrue((self.root / "reports").is_dir())

    def test_sum_with_stdout_closed(self):
        rc, _out = _closed(2, self.root, self.home, "run", "--no-input")
        self.assertEqual(rc, 0)
        rc, err = _closed(1, self.root, self.home, "sum")
        self.assertNotIn("Traceback", err)
        self.assertEqual(rc, 0, err[-2000:])


class TestNamedSymbolsLookups(unittest.TestCase):
    """F8b: named_symbols(lookups=True) holds every symbol a lookup line
    names; the default (the rename rules only) is unchanged."""

    def test_lookup_lines(self):
        from taxjson.bin.taxjson_ticker_map import (_parse_map_file,
                                                    named_symbols)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ticker.map"
            p.write_text("GLOBAL QZA.US QZB.US\n"
                         "QUOTE qzq.us QZQ\n"
                         "T1135 QZT.US USA\n"
                         "CRYPTO QZC QZC-USD\n"
                         "MULT QZM.US 10\n"
                         "STABLE QZS USD\n"
                         "EXTRACT QZ WIDGET FUND | USD | QZE.U.TO\n"
                         "SPLITSHARE QZR\n")
            tm, problems, _n = _parse_map_file(p)
        self.assertEqual(problems, [])
        self.assertEqual(named_symbols(tm), {"QZA.US", "QZB.US"})
        self.assertEqual(named_symbols(tm, lookups=True),
                         {"QZA.US", "QZB.US", "QZQ.US", "QZT.US", "QZC",
                          "QZM.US", "QZS", "QZE.U.TO"})


from test_fix_qt_listing_suffix import _RunBase  # noqa: E402


@rule("CA-XLIST-02")
class TestListingSuffixLookupLinesWin(_RunBase, unittest.TestCase):
    """A QUOTE or T1135 line written for the `.US` spelling keeps the
    row currency's listing (with no map both are read as `.TO`:
    test_fix_qt_listing_suffix.TestRunCanada)."""
    MAP = "QUOTE QZBX.US QZBX\nT1135 QZAX.US USA\n"

    def test_lookup_lines_keep_the_listing(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr[-3000:])
        moved = self.symbols("qt_questrade_transfers")
        self.assertIn(("TRANSFER", "QZBX.US"), moved)
        self.assertIn(("TRANSFER", "QZAX.US"), moved)
        self.assertFalse({s for _a, s in moved} & {"QZAX.TO", "QZBX.TO"})
        from taxjson.lib import listing_suffix as LS
        st = LS.read_state(LS.state_path(self.root / "work", "qt",
                                         "questrade"))
        self.assertEqual(st.get("corrected") or {}, {})
        self.assertIn("ticker.map", st["kept"]["QZBX.US"]["reason"])


if __name__ == "__main__":
    unittest.main()
