"""Re-audit-2 test pins (tests-pins-03), A2-1611: the law citations that
earlier fixes corrected are asserted, so a revert to the wrong section
fails — ITA s.90(1) in taxjson-brokerage (s.90(2) is the foreign-
affiliate rule), the s.86.1(3) spin-off allocation text, the US
significant-holder statements (Reg. §1.368-3 / §1.355-5) in tax-logic,
and line 4 of the US Capital Loss Carryover Worksheet."""
import contextlib
import io
import tempfile
import types
import unittest
from pathlib import Path

from tax_rules import rule


class TestBrokerageCitesS90_1(unittest.TestCase):

    @rule("CA-ACB-08")
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

    @rule("CA-ACB-08")
    def test_no_country_note(self):
        # The INPUTS-03 helper: an IB return of capital from a US issuer,
        # parsed with no --country (a subprocess on these sources).
        from test_partition_engine_inputs import TestStandaloneBrokerageRoc
        r, acts = TestStandaloneBrokerageRoc._brokerage(
            TestStandaloneBrokerageRoc())
        self.assertEqual((r.returncode, acts), (0, ["ADJUST"]), r.stderr)
        self.assertIn("--country canada (ITA s.90(1): a dividend)", r.stderr)
        self.assertNotIn("s.90(2)", r.stderr)


class TestCanadaSpinoffAllocationText(unittest.TestCase):
    @rule("CA-CORP-06")
    def test_s86_1_3_and_form_8937_is_a_us_figure(self):
        from taxjson.lib.corp_actions import CANADA_SPINOFF
        text = " ".join(dict(CANADA_SPINOFF.options)['rollover_s_86_1']
                        .split())
        self.assertIn("combined fair market value right after it "
                      "(s. 86.1(3))", text)
        self.assertIn("Form 8937 percentage is a US figure", text)


class TestUsSignificantHolderStatements(unittest.TestCase):
    @rule("US-CORP-04", "US-CORP-07")
    def test_tax_logic_names_the_regulations(self):
        from taxjson.lib.tax_logic import catalog
        c = catalog("usa")
        self.assertIn("Reg. §1.368-3", c["US-CORP-04"].text)
        self.assertIn("Reg. §1.355-5", c["US-CORP-07"].text)
        self.assertIn("significant holder", c["US-CORP-04"].text)
        self.assertIn("significant distributee", c["US-CORP-07"].text)


class TestUsCarryoverWorksheetLine4(unittest.TestCase):
    def test_carryover_help(self):
        from taxjson.bin.taxjson_carryover import main
        out = io.StringIO()
        with contextlib.redirect_stdout(out), \
                self.assertRaises(SystemExit):
            main(["--help"])
        text = " ".join(out.getvalue().split())
        self.assertIn("as far as taxable income absorbed it (line 4 of "
                      "the next year's Capital Loss Carryover Worksheet",
                      text)
        self.assertIn("not the line 6/14 carryover", text)

    def test_checklist_carryover_step_us(self):
        from taxjson.lib.checklist import d_carryover
        with tempfile.TemporaryDirectory() as td:
            ctx = types.SimpleNamespace(
                root=Path(td), settings={"country": "usa"},
                sub=lambda *a: (0, "{}", ""))
            r = d_carryover(ctx)
        self.assertEqual(r.status, "manual")
        self.assertIn("(Capital Loss Carryover Worksheet line 4), not the "
                      "line 6 / 14 carryover", r.detail)
        with tempfile.TemporaryDirectory() as td:
            ctx = types.SimpleNamespace(
                root=Path(td), settings={"country": "canada"},
                sub=lambda *a: (0, "{}", ""))
            r = d_carryover(ctx)
        self.assertNotIn("Worksheet", r.detail)
        self.assertIn("line 25300", r.detail)


if __name__ == "__main__":
    unittest.main()
