"""GitHub issues #9 (overlapping atomic writers) and #10 (`run --fast`
ignored a change to the shipped package data). (#7, the pre-push binary
scan, is pinned in test_check_pii.py.) Synthetic data only."""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
sys.path.insert(0, str(SRC))


# ------------------------------------------------------------------ #9

class TestOverlappingAtomicWriters(unittest.TestCase):
    """Two atomic_open writers of one final path each own a private temp
    file until their own rename: neither publishes the other's
    unfinished data, and neither fails when the other renames first."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "work" / "m_base.json"
        self.out.parent.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _temps(self):
        return sorted(p.name for p in self.out.parent.iterdir()
                      if p.name != self.out.name)

    def test_interleaved_writers(self):
        from taxjson.lib.safe_write import atomic_open
        a_cm, b_cm = atomic_open(self.out), atomic_open(self.out)
        a = a_cm.__enter__()
        a.write("writer A, complete\n")
        b = b_cm.__enter__()
        b.write("writer B, part 1\n")
        b.flush()
        self.assertEqual(len(self._temps()), 2, self._temps())
        a_cm.__exit__(None, None, None)
        self.assertEqual(self.out.read_text(), "writer A, complete\n")
        b.write("writer B, part 2\n")
        b.flush()
        # B's unfinished data never shows under the final name.
        self.assertEqual(self.out.read_text(), "writer A, complete\n")
        b_cm.__exit__(None, None, None)
        self.assertEqual(self.out.read_text(),
                         "writer B, part 1\nwriter B, part 2\n")
        self.assertEqual(self._temps(), [])

    def test_nested_writers(self):
        from taxjson.lib.safe_write import atomic_open
        with atomic_open(self.out) as outer:
            outer.write("outer\n")
            with atomic_open(self.out) as inner:
                inner.write("inner\n")
            self.assertEqual(self.out.read_text(), "inner\n")
            outer.write("outer, more\n")
        self.assertEqual(self.out.read_text(), "outer\nouter, more\n")
        self.assertEqual(self._temps(), [])

    def test_failed_inner_writer_leaves_the_outer_alone(self):
        from taxjson.lib.safe_write import atomic_open
        with atomic_open(self.out, binary=True) as outer:
            outer.write(b"outer\n")
            with self.assertRaises(RuntimeError):
                with atomic_open(self.out, binary=True) as inner:
                    inner.write(b"half")
                    raise RuntimeError("boom")
        self.assertEqual(self.out.read_bytes(), b"outer\n")
        self.assertEqual(self._temps(), [])

    def test_temp_is_an_owner_only_sibling_with_the_suffix(self):
        from taxjson.lib.safe_write import atomic_open
        with atomic_open(self.out, suffix=".migrate.part") as f:
            f.write("x")
            (name,) = self._temps()
            self.assertTrue(name.startswith("m_base.json."), name)
            self.assertTrue(name.endswith(".migrate.part"), name)
            mode = stat.S_IMODE(os.lstat(self.out.parent / name).st_mode)
            self.assertEqual(mode & 0o077, 0, oct(mode))
        self.assertEqual(stat.S_IMODE(self.out.stat().st_mode) & 0o077, 0)

    def test_keep_mode_still_takes_the_replaced_files_bits(self):
        from taxjson.lib.safe_write import write_atomic
        self.out.write_text("old\n")
        os.chmod(self.out, 0o644)
        write_atomic(self.out, "new\n", keep_mode=True)
        self.assertEqual(stat.S_IMODE(self.out.stat().st_mode), 0o644)
        self.assertEqual(self.out.read_text(), "new\n")

    def test_concurrent_processes_publish_whole_files_only(self):
        # Two processes rewriting one file many times: every read sees
        # one writer's complete text, and no save fails.
        script = (
            "import sys\n"
            "from taxjson.lib.safe_write import atomic_open\n"
            "out, tag = sys.argv[1], sys.argv[2]\n"
            "for i in range(150):\n"
            "    with atomic_open(out) as f:\n"
            "        for _ in range(50):\n"
            "            f.write(tag * 40 + '\\n')\n"
            "            f.flush()\n")
        env = dict(os.environ, PYTHONPATH=str(SRC))
        procs = [subprocess.Popen([sys.executable, "-c", script,
                                   str(self.out), tag], env=env,
                                  stderr=subprocess.PIPE, text=True)
                 for tag in ("a", "b")]
        whole = {(t * 40 + "\n") * 50 for t in ("a", "b")}
        torn = 0
        while any(p.poll() is None for p in procs):
            try:
                text = self.out.read_text()
            except FileNotFoundError:
                continue
            if text not in whole:
                torn += 1
        errs = [p.communicate()[1] for p in procs]
        self.assertEqual(errs, ["", ""])
        self.assertEqual([p.returncode for p in procs], [0, 0])
        self.assertEqual(torn, 0)
        self.assertEqual(self._temps(), [])


# ------------------------------------------------------------------ #10

def _mock_package(root: Path, markets: str) -> Path:
    pkg = root / "taxjson"
    (pkg / "data").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "data" / "markets.toml").write_text(markets)
    return pkg


class TestPackageDataInvalidatesTheFastCache(unittest.TestCase):
    """`run --fast` keys its cache on the package's code; the shipped
    data the code reads (data/markets.toml: venues, index-option roots,
    stablecoins, ...) is part of it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        from taxjson.bin import taxjson_run as R
        self.R = R
        self.pkg = _mock_package(
            self.tmp, '[lists]\nindex_option_roots = ["SYNTH"]\n')
        self._file = mock.patch("taxjson.__file__",
                                str(self.pkg / "__init__.py"))
        self._file.start()
        self._saved_mtime = R._PACKAGE_MTIME_CACHED
        R._PACKAGE_MTIME_CACHED = None

    def tearDown(self):
        self._file.stop()
        self.R._PACKAGE_MTIME_CACHED = self._saved_mtime
        self._tmp.cleanup()

    def test_fingerprint_changes_with_markets_toml(self):
        before = self.R._package_fingerprint()
        data = self.pkg / "data" / "markets.toml"
        st = data.stat()
        data.write_text("[lists]\nindex_option_roots = []\n")
        os.utime(data, ns=(st.st_atime_ns, st.st_mtime_ns))   # cp -p
        self.assertNotEqual(self.R._package_fingerprint(), before)

    def test_fingerprint_sees_a_new_or_removed_data_file(self):
        before = self.R._package_fingerprint()
        extra = self.pkg / "data" / "extra.csv"
        extra.write_text("a,b\n")
        added = self.R._package_fingerprint()
        self.assertNotEqual(added, before)
        extra.unlink()
        self.assertEqual(self.R._package_fingerprint(), before)

    def test_caches_and_editor_litter_do_not_count(self):
        before = self.R._package_fingerprint()
        (self.pkg / "__pycache__").mkdir()
        (self.pkg / "__pycache__" / "x.cpython-312.pyc").write_bytes(b"\0")
        (self.pkg / "data" / ".DS_Store").write_bytes(b"\0")
        (self.pkg / "data" / "markets.toml~").write_text("old")
        (self.pkg / "data" / ".markets.toml.swp").write_bytes(b"\0")
        (self.pkg / "README.md").write_text("docs\n")
        self.assertEqual(self.R._package_fingerprint(), before)

    def test_mtime_scan_sees_markets_toml(self):
        py = self.pkg / "__init__.py"
        data = self.pkg / "data" / "markets.toml"
        os.utime(py, (1000, 1000))
        os.utime(data, (5000, 5000))
        self.assertEqual(self.R._package_mtime(), 5000)


_CONFIG = """\
[settings]
local_timezone = "America/Toronto"
year = 2025
country = "canada"
base_currency = "CAD"
source_currencies = []
option_grant_timing_since = 2025

[accounts.margin]
type = "taxable"
"""

_TT = ("BUYSELL 2025-01-06 10:00:00 ABC.TO 100 CAD 10 -1000 0\n"
       "BUYSELL 2025-03-03 10:00:00 ABC.TO -40 CAD 12 480 0\n")


class TestFastRunRebuildsAfterMarketsChange(unittest.TestCase):
    """Pipeline: a copy of the package whose data/markets.toml changes
    (its mtime kept, as cp -p or an rsync -a upgrade leaves it) makes
    the next `run --fast` rebuild every stage."""

    def test_fast_run_rebuilds(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            lib = td / "lib"
            shutil.copytree(SRC / "taxjson", lib / "taxjson",
                            ignore=shutil.ignore_patterns("__pycache__"))
            root = td / "proj"
            (root / "inputs" / "margin").mkdir(parents=True)
            (root / "taxjson.toml").write_text(_CONFIG)
            (root / "inputs" / "margin" / "a.tt").write_text(_TT)
            env = dict(os.environ, PYTHONPATH=str(lib),
                       TAXJSON_OFFLINE="1", HOME=str(td / "home"),
                       NO_COLOR="1")
            (td / "home").mkdir()

            def run(*a):
                r = subprocess.run(
                    [sys.executable, "-m", "taxjson.bin.taxjson_run", "-C",
                     str(root), "run", "--no-input", *a], cwd=str(td),
                    capture_output=True, text=True, env=env,
                    stdin=subprocess.DEVNULL)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                return r.stdout

            run()
            out = run("--fast")
            self.assertNotIn("code changed", out)
            self.assertNotIn("==> Calculating capital gains", out)
            data = lib / "taxjson" / "data" / "markets.toml"
            st = data.stat()
            with open(data, "a", encoding="utf-8") as f:
                f.write("\n# a market-data-only update\n")
            os.utime(data, ns=(st.st_atime_ns, st.st_mtime_ns))
            out = run("--fast")
            self.assertIn("code changed", out)
            self.assertIn("==> Reading a.tt", out)
            self.assertIn("==> Calculating capital gains", out)


if __name__ == "__main__":
    unittest.main()
