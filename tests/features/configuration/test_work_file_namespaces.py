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


# --------------------------------------- A2-0140 / A2-0402 / A2-0399 / A2-1222
class TestArtifactNamespacesNeverCollide(unittest.TestCase):
    """One account's work files must never be another's: an account named
    <other>_tt_<x> or <other>_<broker> is refused, and two .tt files
    of one account that convert to the same work file are refused.

    Formerly: test_fix_a2_pipecmd.TestArtifactNamespacesNeverCollide"""

    def test_pair_names_refused(self):
        from taxjson.lib.config_check import account_type_problems
        for a, b in (("m", "m_tt_x"), ("a", "a_questrade"),
                     ("a", "a_ib_corp"), ("a", "a_generic-ws"),
                     ("a", "a_rbc_direct")):
            with self.subTest(b=b):
                cfg = {"accounts": {a: {"type": "taxable"},
                                    b: {"type": "taxable"}}}
                probs = account_type_problems(cfg)
                self.assertTrue(any(f"[accounts.{b}]" in p for p in probs),
                                probs)
        ok = {"accounts": {"rrsp": {"type": "sheltered"},
                           "rrsp2": {"type": "sheltered"},
                           "rrsp_spousal": {"type": "sheltered"},
                           "margin": {"type": "taxable"}}}
        self.assertEqual(account_type_problems(ok), [])

    def test_run_refuses_pair(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [], accounts={
                "m": ("taxable", ["BUYSELL 2026-01-05 10:00:00 XYZ.TO 1 "
                                  "CAD 10 -10 0"]),
                "m_tt_x": ("taxable", ["BUYSELL 2026-01-05 10:00:00 "
                                       "ABC.TO 1 CAD 10 -10 0"])})
            r = _cli(root, home, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("[accounts.m_tt_x]", r.stderr)

    def test_tt_suffix_case_twins_refused(self):
        with tempfile.TemporaryDirectory() as td:
            root, home = _tt_project(td, [
                "BUYSELL 2026-01-05 10:00:00 AAA.TO 10 CAD 10 -100 0"])
            twin = root / "inputs" / "m" / "a.TT"
            twin.write_text("BUYSELL 2026-01-05 10:00:00 BBB.TO 10 CAD 10 "
                            "-100 0\n")
            if len(list((root / "inputs" / "m").iterdir())) < 2:
                self.skipTest("case-insensitive file system")
            r = _cli(root, home, "run", "--no-input")
            self.assertNotEqual(r.returncode, 0, r.stdout[-1500:])
            self.assertIn("a.TT", r.stderr)
            self.assertIn("a.tt", r.stderr)


if __name__ == "__main__":
    unittest.main()
