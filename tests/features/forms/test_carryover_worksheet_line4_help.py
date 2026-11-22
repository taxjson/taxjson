"""Re-audit-2 test pins (tests-pins-03), A2-1611: the law citations that
earlier fixes corrected are asserted, so a revert to the wrong section
fails — ITA s.90(1) in taxjson-brokerage (s.90(2) is the foreign-
affiliate rule), the s.86.1(3) spin-off allocation text, the US
significant-holder statements (Reg. §1.368-3 / §1.355-5) in tax-logic,
and line 4 of the US Capital Loss Carryover Worksheet.

Formerly: test_fix_a2_pins1_03_citations"""
import contextlib
import io
import tempfile
import types
import unittest
from pathlib import Path

from taxjson.project.settings import Settings


class TestUsCarryoverWorksheetLine4(unittest.TestCase):
    """Formerly: test_fix_a2_pins1_03_citations.TestUsCarryoverWorksheetLine4"""
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
                root=Path(td), settings=Settings.from_table({"country": "usa"}),
                sub=lambda *a: (0, "{}", ""))
            r = d_carryover(ctx)
        self.assertEqual(r.status, "manual")
        self.assertIn("(Capital Loss Carryover Worksheet line 4), not the "
                      "line 6 / 14 carryover", r.detail)
        with tempfile.TemporaryDirectory() as td:
            ctx = types.SimpleNamespace(
                root=Path(td), settings=Settings.from_table({"country": "canada"}),
                sub=lambda *a: (0, "{}", ""))
            r = d_carryover(ctx)
        self.assertNotIn("Worksheet", r.detail)
        self.assertIn("line 25300", r.detail)


if __name__ == "__main__":
    unittest.main()
