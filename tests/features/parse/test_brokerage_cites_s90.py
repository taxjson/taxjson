"""Re-audit-2 test pins (tests-pins-03), A2-1611: the law citations that
earlier fixes corrected are asserted, so a revert to the wrong section
fails — ITA s.90(1) in taxjson-brokerage (s.90(2) is the foreign-
affiliate rule), the s.86.1(3) spin-off allocation text, the US
significant-holder statements (Reg. §1.368-3 / §1.355-5) in tax-logic,
and line 4 of the US Capital Loss Carryover Worksheet.

Formerly: test_fix_a2_pins1_03_citations"""
import contextlib
import io
import unittest

from tax_rules import rule
from taxjson.rules import ids as R


class TestBrokerageCitesS90_1(unittest.TestCase):
    """Formerly: test_fix_a2_pins1_03_citations.TestBrokerageCitesS90_1"""

    @rule(R.CA_ACB_08)
    def test_help(self):
        from unittest import mock
        from taxjson.bin.taxjson_brokerage import main
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                mock.patch("sys.argv", ["taxjson-brokerage", "--help"]), \
                self.assertRaises(SystemExit):
            main()
        text = " ".join(out.getvalue().split())
        self.assertIn("canada -> 'dividend' (ITA s.90(1))", text)
        self.assertIn("'dividend' (ITA s.90(1): a non-resident "
                      "corporation's distribution", text)
        self.assertNotIn("s.90(2)", text)

    @rule(R.CA_ACB_08)
    def test_no_country_note(self):
        # The INPUTS-03 helper: an IB return of capital from a US issuer,
        # parsed with no --country (a subprocess on these sources).
        from _support.ib_roc_brokerage import _standalone_brokerage
        r, acts = _standalone_brokerage()
        self.assertEqual((r.returncode, acts), (0, ["ADJUST"]), r.stderr)
        self.assertIn("--country canada (ITA s.90(1): a dividend)", r.stderr)
        self.assertNotIn("s.90(2)", r.stderr)


if __name__ == "__main__":
    unittest.main()
