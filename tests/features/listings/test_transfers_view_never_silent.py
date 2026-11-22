"""Regression pins for the re-audit-2 pipeline / misc-command findings.

Synthetic projects only (fake account names, hand-written FX rates under
work/, TAXJSON_OFFLINE set), so nothing here touches the network.

Formerly: test_fix_a2_pipecmd
"""
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


# ------------------------------------------------------ A2-0717 / A2-1232
class TestTransfersViewNeverSilent(unittest.TestCase):
    """Formerly: test_fix_a2_pipecmd.TestTransfersViewNeverSilent"""

    def test_missing_config_and_base(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [], accounts={
                "margin": ("taxable", [
                    "BUYSELL 2026-01-05 10:00:00 XYZ.TO 10 CAD 10 -100 0"]),
                "rrsp": ("sheltered", [
                    "BUYSELL 2026-01-05 10:00:00 XYZ.TO 100 CAD 10 -1000 0",
                    "TRANSFER 2026-02-05 10:00:00 XYZ.TO -100 CAD 10 1000 "
                    "DECLARED"])}, extra_settings="")
            cfg = root / "taxjson.toml"
            cfg.write_text(cfg.read_text().replace(
                '[accounts.rrsp]\ntype = "sheltered"\n',
                '[accounts.rrsp]\ntype = "sheltered"\ntransfers = true\n'))
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            r = _cli(root, home, "transfers")
            self.assertIn("XYZ.TO", r.stdout, r.stderr)
            base = root / "work" / "rrsp_base.json"
            data = base.read_bytes()
            base.unlink()
            r = _cli(root, home, "transfers")
            self.assertIn("rrsp_base.json", r.stderr)
            base.write_bytes(data)
            cfg.rename(root / "moved.toml")
            r = _cli(root, home, "transfers")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("taxjson.toml", r.stderr)


if __name__ == "__main__":
    unittest.main()
