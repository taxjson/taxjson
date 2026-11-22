"""Regression pins for the re-audit-2 pipeline / misc-command findings.

Synthetic projects only (fake account names, hand-written FX rates under
work/, TAXJSON_OFFLINE set), so nothing here touches the network.

Formerly: test_fix_a2_pipecmd
"""
import os
import tempfile
import unittest

from _support.tt_fx_project import _cli, _tt_project
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ------------------------------------- A2-0143 / A2-0403 / A2-0144 / A2-0401
class TestUnreadableInputsAreRefused(unittest.TestCase):
    """A file that exists as a name but cannot be read (a dangling or
    looping symlink, a directory) is never taken as absent.

    Formerly: test_fix_a2_pipecmd.TestUnreadableInputsAreRefused"""

    def _proj(self, td):
        return _tt_project(td, [
            "BUYSELL 2026-01-05 10:00:00 XYZ.TO 10 CAD 10 -100 0"])

    def test_dangling_statement_csv(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            (root / "inputs" / "m" / "questrade_h2.csv").symlink_to(
                root / "nowhere.csv")
            r = _cli(root, home, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0, r.stdout[-1500:])
            self.assertIn("questrade_h2.csv", r.stderr)

    def test_dangling_ticker_map_in_read_commands(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            (root / "ticker.map").symlink_to(root / "gone.map")
            for cmd in (["sanity"], ["tips"], ["harvest"], ["fees"]):
                with self.subTest(cmd=cmd):
                    r = _cli(root, home, *cmd)
                    self.assertNotEqual(r.returncode, 0, r.stdout[-800:])
                    self.assertIn("ticker.map", r.stderr)

    def test_dangling_config_is_not_absent(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            cfg = root / "taxjson.toml"
            cfg.rename(root / "moved.toml")
            cfg.symlink_to(root / "gone.toml")
            r = _cli(root, home, "fees")
            self.assertNotEqual(r.returncode, 0, r.stdout[-800:])
            self.assertIn("taxjson.toml", r.stderr)


# --------------------------------------------- A2-0713 / A2-1228 / A2-0714
class TestUnusableStatementFiles(unittest.TestCase):
    """Formerly: test_fix_a2_pipecmd.TestUnusableStatementFiles"""

    def _proj(self, td):
        return _tt_project(td, [
            "BUYSELL 2026-01-05 10:00:00 XYZ.TO 10 CAD 10 -100 0"])

    def test_empty_csv(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            (root / "inputs" / "m" / "questrade_2026.csv").write_text("")
            r = _cli(root, home, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("is empty", r.stderr)
            self.assertNotIn("Rename to start", r.stderr)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0,
                     "root reads any file")
    def test_unreadable_csv(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            f = root / "inputs" / "m" / "questrade_2026.csv"
            f.write_text("x,y\n1,2\n")
            f.chmod(0)
            try:
                r = _cli(root, home, "run", "--no-input")
            finally:
                f.chmod(0o600)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("permission denied", r.stderr.lower())
            self.assertIn("chmod u+r", r.stderr)
            self.assertNotIn("Rename to start", r.stderr)

    def test_numbers_export_is_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = self._proj(td)
            (root / "inputs" / "m" / "Activity_2026.numbers").write_bytes(
                b"PK\x03\x04")
            r = _cli(root, home, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("Activity_2026.numbers", r.stderr)


if __name__ == "__main__":
    unittest.main()
