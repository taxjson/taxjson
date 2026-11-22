"""Regression tests for fix round 7 (wave E), group us: the US engine and
US output.

TJ-1261 the silent planning walk used up the engine's said-once notes.
TJ-1262 one `+?` cost turned `tjs list`'s money columns to text.
TJ-1263 `tjs events` sized its PRICE and TOTAL columns to corp= tokens.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_1029_r7e_us
"""
import tempfile
import unittest

import _qa_project as qa


# --------------------------------------------------------------- TJ-1262
class TestListKeepsMoneyRightAligned(unittest.TestCase):
    """Formerly: test_fix_1029_r7e_us.TestListKeepsMoneyRightAligned"""
    ROWS = ['OPENING 2024-12-31 ZZOP.US 10 cost=unknown reason="before the files"',
            "BUYSELL 2025-01-10 10:00:00 ZZC.US 100 USD 30 3000 0",
            "BUYSELL 2025-01-10 10:00:00 ZZD.US 33 USD 12.5 412.5 0"]

    def _list_lines(self, country):
        with tempfile.TemporaryDirectory() as td:
            root = qa.project(td, "p", {"margin/trades.tt":
                                        "\n".join(self.ROWS) + "\n"},
                              country=country)
            qa.tj(root, "run", "--no-input")
            out = qa.tj(root, "list").stdout
        return [ln for ln in out.splitlines()
                if ln.startswith(("ACCOUNT", "margin"))]

    def test_both_countries(self):
        for country in ("usa", "canada"):
            lines = self._list_lines(country)
            head = lines[0]
            end = head.index("COST/SH") + len("COST/SH")
            cost_end = head.index("COST/SH") - 2
            for ln in lines[1:]:
                self.assertIn(ln[cost_end - 1], "0123456789?", (country, ln))
                self.assertIn(ln[end - 1], "0123456789?", (country, ln))
                self.assertEqual(ln[end:end + 1], " ", (country, ln))
            self.assertTrue(any("+?" in ln for ln in lines), lines)

    def test_a_cell_of_text_still_makes_a_text_column(self):
        from taxjson.lib.report_fmt import auto_aligns
        self.assertEqual(auto_aligns(["A"], [["0.00+?"], ["1,234.50"]]), [">"])
        self.assertEqual(auto_aligns(["A"], [["+?"], ["1,234.50"]]), ["<"])
        self.assertEqual(auto_aligns(["A"], [["0.00?"], ["1,234.50"]]), ["<"])
        self.assertEqual(auto_aligns(["A"], [["ZZ+?"], ["1,234.50"]]), ["<"])


if __name__ == "__main__":
    unittest.main()
