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

from tax_rules.dual import tx
from _support.us_reports import _gains, _paid, _project, _tjs

from _support.us_short_rows import _short


# --------------------------------------------------------------- TJ-1261
class TestSaidOnceNotesSurviveThePlan(unittest.TestCase):
    """Formerly: test_fix_1029_r7e_us.TestSaidOnceNotesSurviveThePlan"""
    OPTION = ["BUYSELL 2025-01-20 10:00:00 ZOPT250321C00010000.US -2 USD 1 200 0",
              "BUYSELL 2025-02-03 10:00:00 ZOPT.US 200 USD 11 2200 0",
              "ASSIGN 2025-03-21 16:00:00 ZOPT250321C00010000.US 2 USD 0 0 0",
              "ASSIGN 2025-03-21 16:00:01 ZOPT.US -200 USD 10 2000 0"]
    PAID = ["BUYSELL 2025-01-06 10:00:00 ZQSH.US -100 USD 10 1000 0",
            "DIVIDEND_IN_LIEU 2025-01-15 16:00:00 ZQSH.US 0 USD 0 -20.00",
            "BUYSELL 2025-01-21 10:00:00 ZQSH.US 100 USD 9 900 0"]

    def _run(self, rows):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, rows, year=2025)
            _tjs(root, "run", "--no-input")
            diag = (root / "work" / "margin_gains.json.diag").read_text()
            carry = _tjs(root, "carryover").stderr
            return diag, carry

    def test_the_contract_size_note_is_said_once(self):
        for rows in (self.OPTION, self.OPTION + self.PAID):
            diag, carry = self._run(rows)
            self.assertEqual(diag.count("ZOPT options"), 1, rows)
            self.assertEqual(carry.count("ZOPT options"), 1, rows)

    def test_a_split_date_note_is_said_once(self):
        from taxjson.lib import corporate_timeline as ct
        from taxjson.lib import markets
        rows = [tx("BUYSELL", "2025-01-02", "ZQS.US", 100, 900.0),
                tx("SPLIT", "2025-02-03", "ZQS.US", 2.0, 0.0,
                   symbol_new="ZQS.US", account="margin"),
                tx("SPLIT", "2025-02-04", "ZQS.US", 2.0, 0.0,
                   symbol_new="ZQS.US"),
                *_short("2025-05-01", "2025-06-12", sym="ZQT.US"),
                _paid("2025-05-15", "ZQT.US", -20.0)]
        ct._SPLIT_DATE_NOTES.clear()
        markets.reset_notes()
        err = _gains(rows)["_stderr"]
        self.assertEqual(err.count("is booked on two dates"), 1, err)

    def test_notes_said_before_the_plan_stay_said(self):
        from taxjson.lib import markets
        from taxjson.engine.us.taxrules import USATaxRules
        markets.reset_notes()
        self.addCleanup(markets.reset_notes)
        markets._NOTED.add(("earlier", "note", None))
        USATaxRules()._plan_paid_compensation(
            [*_short("2025-05-01", "2025-06-12"),
             _paid("2025-05-15", "ZQA.US", -20.0)], [], [])
        self.assertIn(("earlier", "note", None), markets._NOTED)
        self.assertEqual(len(markets._NOTED), 1)


if __name__ == "__main__":
    unittest.main()
