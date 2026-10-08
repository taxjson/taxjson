"""GitHub issue #9 (overlapping atomic writers). (#7, the pre-push
binary scan, is pinned in test_check_pii.py.) Synthetic data only."""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
