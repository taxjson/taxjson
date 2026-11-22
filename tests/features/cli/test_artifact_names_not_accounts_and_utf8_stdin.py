"""Regression pins for the re-audit-2 pipeline / misc-command findings.

Synthetic projects only (fake account names, hand-written FX rates under
work/, TAXJSON_OFFLINE set), so nothing here touches the network.

Formerly: test_fix_a2_pipecmd
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _support.tt_fx_project import REPO_ROOT, _env, _cli, _tt_project
from _style import CapturedWidth


# Captured output (TAXJSON_WIDTH=0, as scripts/ci.sh runs the suite):
# the module passes run alone too (_style.CapturedWidth).
_WIDTH = CapturedWidth()


def setUpModule():
    _WIDTH.start()


def tearDownModule():
    _WIDTH.stop()


# ---------------------------------------------------------------- A2-0394
class TestArtifactNamesAreNotAccounts(unittest.TestCase):
    """sum / list / winners / wash-sales took `margin_raw` (the native-
    currency books) as an account and printed USD under a CAD header.

    Formerly: test_fix_a2_pipecmd.TestArtifactNamesAreNotAccounts"""

    def test_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [
                "BUYSELL 2026-01-05 10:00:00 XYZ.US 10 USD 10 -100 0",
                "BUYSELL 2026-03-05 10:00:00 XYZ.US -10 USD 12 120 0"])
            r = _cli(root, home, "run", "--no-input")
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            for cmd in ("sum", "list", "winners", "wash-sales"):
                for acct in ("m_raw", "m_raw_base"):
                    with self.subTest(cmd=cmd, acct=acct):
                        r = _cli(root, home, cmd, acct)
                        self.assertNotEqual(r.returncode, 0, r.stdout[-600:])
                        self.assertIn("not an account", r.stderr)
            r = _cli(root, home, "sum", "m")
            self.assertEqual(r.returncode, 0, r.stderr[-800:])


# ---------------------------------------------------------------- A2-1219
class TestStdinIsUtf8(unittest.TestCase):
    """Formerly: test_fix_a2_pipecmd.TestStdinIsUtf8"""

    def test_piped_json_under_an_ascii_locale(self):
        doc = json.dumps({"transactions": [{
            "action": "DIVIDEND", "date": "2026-03-03", "time": "10:00:00",
            "symbol": "GLE.PA", "quantity": 0, "price": 0,
            "net_amount": 10.0, "gross_amount": 10.0, "currency": "EUR",
            "account": "m", "description": "Société Générale"}]},
            ensure_ascii=False).encode("utf-8")
        for mod in ("taxjson.bin.taxjson_sort", "taxjson.bin.taxjson_sum_income"):
            with self.subTest(mod=mod):
                r = subprocess.run(
                    [sys.executable, "-m", mod], cwd=REPO_ROOT,
                    input=doc, capture_output=True,
                    env=_env(Path("/nonexistent"), PYTHONIOENCODING="ascii:backslashreplace",
                             LC_ALL="C", LANG="C"))
                self.assertEqual(r.returncode, 0, r.stderr.decode(
                    "utf-8", "replace"))


if __name__ == "__main__":
    unittest.main()
