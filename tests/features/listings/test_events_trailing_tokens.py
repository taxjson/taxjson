"""Regression tests for fix round 7 (wave E), group us: the US engine and
US output.

TJ-1261 the silent planning walk used up the engine's said-once notes.
TJ-1262 one `+?` cost turned `tjs list`'s money columns to text.
TJ-1263 `tjs events` sized its PRICE and TOTAL columns to corp= tokens.

Every fixture is synthetic: invented tickers, amounts and account ids.

Formerly: test_fix_1029_r7e_us
"""
import re
import tempfile
import unittest

from _support.us_reports import _project, _tjs


# --------------------------------------------------------------- TJ-1263
SPIN_CSV = """Statement,Header,Field Name,Field Value
Statement,Data,BrokerName,Interactive Brokers
Statement,Data,Title,Activity Statement
Statement,Data,Period,"January 1, 2025 - December 31, 2025"
Corporate Actions,Header,Asset Category,Currency,Report Date,Date/Time,Description,Quantity,Proceeds,Value,Realized P/L,Code
Corporate Actions,Data,Stocks,USD,2025-02-03,"2025-02-03, 10:00:00","ZZQQ(US0000000921) Spinoff 1 for 1 (ZZKK, ZZKK INC, US0000000922)",100,0,100,0,
"""


class TestEventsTrailingTokens(unittest.TestCase):
    """Formerly: test_fix_1029_r7e_us.TestEventsTrailingTokens"""
    ROWS = ["BUYSELL 2025-01-17 10:00:00 ZZQQ.US 100 USD 2 200 0",
            "BUYSELL 2025-03-03 10:00:00 ZZQQ.US -100 USD 1 100 0",
            "BUYSELL 2025-03-04 10:00:00 ZZKK.US -100 USD 1 100 0"]

    def _events(self, *view):
        with tempfile.TemporaryDirectory() as td:
            root = _project(td, self.ROWS, year=2025, csv=SPIN_CSV)
            first = _tjs(root, "run", "--no-input", ok=False)
            m = re.search(r"20250203-zzqq-zzkk-[0-9a-f]+",
                          first.stdout + first.stderr)
            self.assertIsNotNone(m, first.stderr[-1500:])
            _tjs(root, "elect", "margin", "--set",
                 f"{m.group(0)}=tax_free_355",
                 "--hint", "allocated_acb=90")
            _tjs(root, "run", "--no-input")
            return _tjs(root, "events", *view).stdout

    def test_the_tokens_do_not_size_the_numbers(self):
        out = self._events()
        rows = [ln for ln in out.splitlines() if " ZZ" in ln]
        self.assertTrue(any("corp=" in ln for ln in rows), out)
        self.assertTrue(any("election=tax_free_355" in ln for ln in rows), out)
        plain = next(ln for ln in rows
                     if " ZZQQ.US 100 " in ln and "corp=" not in ln)
        self.assertLess(len(plain), 80, plain)
        self.assertRegex(plain, r"USD +2 +200\.00 +0\.00$")

    def test_the_tokens_start_in_one_column(self):
        out = self._events()
        starts = {ln.index(" corp=") for ln in out.splitlines()
                  if " corp=" in ln}
        self.assertEqual(len(starts), 1, out)

    def test_view_helper_keeps_every_token(self):
        from taxjson.cli.listings import _align_tx_lines
        lines = ["BUYSELL 2025-01-17 10:00:00 ZQ.US 100 USD 2 200.00 0.00",
                 "BUYSELL 2025-02-03 10:00:00 ZK.US 100 USD 0.9 90.00 0.00 "
                 "corp=a election=b",
                 "ADJUST 2025-02-03 10:00:00 ZQ.US USD -90.00 corp=a",
                 "BUYSELL 2025-03-03 10:00:00 ES=F.US 1 USD 1 1.00 0.00 x50",
                 "ASSIGN 2025-03-03 10:00:00 ZW.US 1 USD 1 1.00 0.00 x2 "
                 "exercise_of=ZW"]
        out = _align_tx_lines(lines)
        self.assertEqual([ln.split() for ln in out],
                         [ln.split() for ln in lines])

    def test_no_tokens_is_the_old_layout(self):
        from taxjson.cli.listings import _align_tx_lines
        from taxjson.lib.report_fmt import align_columns
        lines = ["BUYSELL 2025-01-17 10:00:00 ZQ.US 100 USD 2 200.00 0.00",
                 "ADJUST 2025-02-03 10:00:00 ZQ.US USD -90.00",
                 "BUYSELL 2025-03-03 10:00:00 ES=F.US 1 USD 12.5 1.00 0.00"]
        self.assertEqual(_align_tx_lines(lines),
                         align_columns(lines, numeric_right=True, header=False))

    def test_a_pasted_listing_reads_back(self):
        from taxjson.cli.listings import _align_tx_lines
        from taxjson.lib import tt_format as C
        lines = ["BUYSELL 2025-02-03 10:00:00 ZK.US 100 USD 0.9 90.00 0.00 "
                 "corp=ev-1 election=tax_free_355",
                 "ADJUST 2025-02-03 10:00:00 ZQ.US USD -90.00 corp=ev-1 "
                 "election=tax_free_355"]
        self.assertEqual([C.parse_tt_line(ln) for ln in lines],
                         [C.parse_tt_line(ln)
                          for ln in _align_tx_lines(lines)])


if __name__ == "__main__":
    unittest.main()
