"""GitHub issue #29 (synthetic child processes only): cra_slips.
_read_capped's one deadline covers the reading AND the process's exit.
"""
import subprocess
import sys
import time
import unittest


def _child(code: str) -> "subprocess.Popen":
    return subprocess.Popen([sys.executable, "-c", code],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL)


class TestReadCapped(unittest.TestCase):
    """#29."""

    def test_child_closes_stdout_then_runs_on(self):
        from taxjson.lib.cra_slips import _read_capped
        p = _child("import os, time; os.close(1); time.sleep(30)")
        start = time.monotonic()
        out = _read_capped(p, 100, 0.5)
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual(out, (None, False))
        self.assertIsNotNone(p.returncode)          # killed and reaped
        self.assertTrue(p.stdout.closed)

    def test_blocked_reader(self):
        from taxjson.lib.cra_slips import _read_capped
        p = _child("import time; time.sleep(30)")
        start = time.monotonic()
        self.assertEqual(_read_capped(p, 100, 0.5), (None, False))
        self.assertLess(time.monotonic() - start, 10)
        self.assertIsNotNone(p.returncode)
        self.assertTrue(p.stdout.closed)

    def test_oversize_output(self):
        from taxjson.lib.cra_slips import _read_capped
        p = _child("import sys, time\nsys.stdout.write('x' * 300000)\n"
                   "sys.stdout.flush()\ntime.sleep(30)")
        start = time.monotonic()
        out, over = _read_capped(p, 1000, 20)
        self.assertTrue(over)
        self.assertEqual(len(out), 1000)
        self.assertLess(time.monotonic() - start, 10)
        self.assertIsNotNone(p.returncode)
        self.assertTrue(p.stdout.closed)

    def test_normal_exit(self):
        from taxjson.lib.cra_slips import _read_capped
        p = _child("print('slip')")
        self.assertEqual(_read_capped(p, 1000, 20), (b"slip\n", False))
        self.assertEqual(p.returncode, 0)
        self.assertTrue(p.stdout.closed)


if __name__ == "__main__":
    unittest.main()
